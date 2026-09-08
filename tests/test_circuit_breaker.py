"""Tests for the persistent circuit breaker that pauses after consecutive failures.

These tests pin the public contract of
:mod:`jobtrail_ai_scorer.circuit_breaker`:

* :class:`BreakerConfig` carries the threshold, cooldowns, and state path.
* :class:`BreakerState` carries the persisted counters.
* :class:`CircuitBreaker` exposes:

  - :meth:`should_attempt` — True iff the breaker is CLOSED, or HALF_OPEN
    (cooldown elapsed since ``opened_at``).
  - :meth:`record_failure` — increment the consecutive-failure counter and
    open the breaker at the threshold. ``last_alert_at`` is *not* updated
    by this call.
  - :meth:`record_success` — close the breaker, reset the counter, and
    reset the alert timer so the next :meth:`try_alert` returns True.
  - :meth:`try_alert` — return True iff no alert has been emitted within
    ``alert_cooldown_seconds``; on True, ``last_alert_at`` is updated.

* State persists across :class:`CircuitBreaker` re-instantiations via the
  same atomic-JSON helper that backs :class:`SeenCache`.
* A corrupt state file degrades to defaults (consecutive_failures == 0,
  opened_at == None, last_alert_at == None) so storage failures never
  crash the automation.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from jobtrail_ai_scorer.circuit_breaker import (
    DEFAULT_BREAKER_STATE_PATH,
    BreakerConfig,
    BreakerState,
    CircuitBreaker,
)


# --- Fake clock --------------------------------------------------------------


class _FakeClock:
    """Deterministic clock for state-transition tests.

    ``advance(seconds)`` moves the clock forward by ``seconds``. The default
    clock that backs :class:`CircuitBreaker` calls a zero-arg callable, so the
    fake clock is just an instance whose ``__call__`` returns ``self.now``.
    """

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_breaker(
    tmp_path: Path,
    *,
    threshold: int = 3,
    cooldown: float = 60.0,
    alert_cooldown: float = 60.0,
    start: float = 0.0,
) -> tuple[CircuitBreaker, _FakeClock, Path]:
    """Build a CircuitBreaker + clock + path triple for one test."""

    clock = _FakeClock(start)
    path = tmp_path / "breaker.json"
    config = BreakerConfig(
        failure_threshold=threshold,
        cooldown_seconds=cooldown,
        alert_cooldown_seconds=alert_cooldown,
        state_path=str(path),
    )
    return CircuitBreaker(config, clock=clock), clock, path


# --- Constants ----------------------------------------------------------------


def test_default_breaker_state_path_is_outside_repo_and_under_logs():
    """The default state path must mirror SeenCache's runtime logs location."""

    assert DEFAULT_BREAKER_STATE_PATH == (
        "/DATA/AppData/jobtrail/logs/automated-job-search/breaker.json"
    )
    assert "jobtrail-ai-scorer" not in DEFAULT_BREAKER_STATE_PATH
    assert DEFAULT_BREAKER_STATE_PATH.endswith("/automated-job-search/breaker.json")


def test_breaker_config_defaults_match_design():
    """The dataclass defaults match the design (3 failures / 1h cooldowns)."""

    config = BreakerConfig()
    assert config.failure_threshold == 3
    assert config.cooldown_seconds == 3600.0
    assert config.alert_cooldown_seconds == 3600.0
    assert config.state_path == DEFAULT_BREAKER_STATE_PATH


def test_breaker_state_defaults_are_closed():
    """A fresh ``BreakerState`` is CLOSED with no open/alert timestamp."""

    state = BreakerState()
    assert state.consecutive_failures == 0
    assert state.opened_at is None
    assert state.last_alert_at is None


# --- Initial state ------------------------------------------------------------


def test_fresh_breaker_is_closed_and_allows_attempts(tmp_path: Path):
    """A new CircuitBreaker is CLOSED with counter == 0 and should_attempt == True."""

    breaker, _clock, _path = _make_breaker(tmp_path)
    assert breaker.state.consecutive_failures == 0
    assert breaker.state.opened_at is None
    assert breaker.should_attempt() is True


def test_single_failure_below_threshold_keeps_breaker_closed(tmp_path: Path):
    """A failure below the threshold does not open the breaker."""

    breaker, _clock, _path = _make_breaker(tmp_path, threshold=3)
    breaker.record_failure()
    assert breaker.state.consecutive_failures == 1
    assert breaker.state.opened_at is None
    assert breaker.should_attempt() is True


# --- CLOSED -> OPEN ----------------------------------------------------------


def test_threshold_consecutive_failures_open_the_breaker(tmp_path: Path):
    """Three consecutive failures transition the breaker to OPEN."""

    breaker, _clock, _path = _make_breaker(tmp_path, threshold=3, start=10.0)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.should_attempt() is True  # 2 failures: still CLOSED

    breaker.record_failure()
    state = breaker.state
    assert state.consecutive_failures == 3
    assert state.opened_at == 10.0
    assert breaker.should_attempt() is False  # OPEN: blocked


def test_record_failure_uses_clock_when_opening(tmp_path: Path):
    """The opened_at timestamp reflects the clock value at opening time."""

    breaker, clock, _path = _make_breaker(tmp_path, threshold=2, start=0.0)
    clock.advance(42.0)  # now t=42
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state.opened_at == 42.0


# --- OPEN / cooldown ---------------------------------------------------------


def test_open_breaker_blocks_attempts_during_cooldown(tmp_path: Path):
    """While within cooldown, should_attempt returns False."""

    breaker, clock, _path = _make_breaker(
        tmp_path, threshold=2, cooldown=60.0, start=0.0
    )
    breaker.record_failure()
    breaker.record_failure()  # opens at t=0, cooldown=60
    assert breaker.should_attempt() is False

    # Halfway through cooldown: still blocked.
    clock.advance(30.0)
    assert breaker.should_attempt() is False


def test_half_open_attempts_allowed_after_cooldown_elapses(tmp_path: Path):
    """After cooldown elapses, should_attempt returns True (HALF_OPEN)."""

    breaker, clock, _path = _make_breaker(
        tmp_path, threshold=2, cooldown=60.0, start=0.0
    )
    breaker.record_failure()
    breaker.record_failure()  # opens at t=0
    clock.advance(60.0)  # exactly cooldown elapsed
    assert breaker.should_attempt() is True

    clock.advance(120.0)
    assert breaker.should_attempt() is True


# --- HALF_OPEN transitions ----------------------------------------------------


def test_half_open_success_closes_breaker_and_resets_counter(tmp_path: Path):
    """A success during HALF_OPEN closes the breaker (counter reset, recovery)."""

    breaker, clock, _path = _make_breaker(
        tmp_path, threshold=2, cooldown=60.0, start=0.0
    )
    breaker.record_failure()
    breaker.record_failure()  # opens at t=0

    clock.advance(60.0)  # HALF_OPEN
    assert breaker.should_attempt() is True

    breaker.record_success()
    state = breaker.state
    assert state.consecutive_failures == 0
    assert state.opened_at is None
    assert breaker.should_attempt() is True  # CLOSED again


def test_half_open_failure_reopens_breaker_without_alerting(tmp_path: Path):
    """A failure during HALF_OPEN reopens the breaker; no auto-alert is fired."""

    breaker, clock, _path = _make_breaker(
        tmp_path, threshold=2, cooldown=60.0, alert_cooldown=120.0, start=0.0
    )
    breaker.record_failure()
    breaker.record_failure()  # opens at t=0

    # Fire an initial alert (caller-driven). Within alert_cooldown.
    assert breaker.try_alert() is True
    assert breaker.state.last_alert_at == 0.0

    clock.advance(60.0)  # HALF_OPEN
    assert breaker.should_attempt() is True

    breaker.record_failure()  # reopen at t=60
    state = breaker.state
    assert state.consecutive_failures == 3
    assert state.opened_at == 60.0
    assert breaker.should_attempt() is False  # back to OPEN

    # record_failure did NOT update last_alert_at on its own. The earlier
    # alert is still within alert_cooldown so try_alert returns False.
    assert state.last_alert_at == 0.0
    assert breaker.try_alert() is False


def test_record_failure_does_not_fire_alert(tmp_path: Path):
    """``record_failure`` is alert-free; ``last_alert_at`` stays ``None``."""

    breaker, _clock, _path = _make_breaker(tmp_path, threshold=1, cooldown=60.0)
    breaker.record_failure()  # opens the breaker
    # ``record_failure`` opens the breaker but never stamps ``last_alert_at``.
    # Alerting is the caller's responsibility via ``try_alert``.
    assert breaker.state.last_alert_at is None


def test_repeated_failures_during_open_do_not_stamp_alert(tmp_path: Path):
    """Failures while OPEN do not update ``last_alert_at`` on their own."""

    breaker, _clock, _path = _make_breaker(tmp_path, threshold=2, cooldown=60.0)
    breaker.record_failure()
    breaker.record_failure()  # opens at t=0

    # Caller fires one alert; the stamp is recorded.
    assert breaker.try_alert() is True
    first_alert_at = breaker.state.last_alert_at
    assert first_alert_at is not None

    # Additional failures (programmer error: caller shouldn't be running while
    # the breaker is OPEN, but if they do) MUST NOT re-stamp the alert timer.
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state.last_alert_at == first_alert_at


# --- try_alert cadence --------------------------------------------------------


def test_try_alert_returns_true_once_per_alert_cooldown(tmp_path: Path):
    """try_alert gates on alert_cooldown_seconds; second call within window is False."""

    breaker, clock, _path = _make_breaker(
        tmp_path, threshold=1, cooldown=60.0, alert_cooldown=60.0, start=0.0
    )
    breaker.record_failure()  # opens at t=0

    # First call: no prior alert, returns True and stamps last_alert_at.
    assert breaker.try_alert() is True
    assert breaker.state.last_alert_at == 0.0

    # Second call within alert_cooldown: blocked.
    clock.advance(30.0)
    assert breaker.try_alert() is False
    # last_alert_at is not updated on a blocked call.
    assert breaker.state.last_alert_at == 0.0

    # After alert_cooldown elapses: another alert is allowed.
    clock.advance(31.0)  # t=61
    assert breaker.try_alert() is True
    assert breaker.state.last_alert_at == 61.0


def test_try_alert_starts_unblocked_for_fresh_breaker(tmp_path: Path):
    """With no prior alert, the very first try_alert returns True."""

    breaker, _clock, _path = _make_breaker(tmp_path)
    assert breaker.state.last_alert_at is None
    assert breaker.try_alert() is True


# --- record_success semantics -------------------------------------------------


def test_record_success_does_not_increment_counter(tmp_path: Path):
    """``record_success`` resets the counter to 0 (it never increments)."""

    breaker, _clock, _path = _make_breaker(tmp_path, threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state.consecutive_failures == 2

    breaker.record_success()
    assert breaker.state.consecutive_failures == 0


def test_record_success_resets_alert_timer_so_recovery_alert_fires(tmp_path: Path):
    """After recovery, the next ``try_alert`` returns True (recovery alert)."""

    breaker, clock, _path = _make_breaker(
        tmp_path, threshold=2, cooldown=60.0, alert_cooldown=60.0, start=0.0
    )
    breaker.record_failure()
    breaker.record_failure()  # opens at t=0
    assert breaker.try_alert() is True  # initial outage alert fires

    # Still within alert_cooldown: no second alert yet.
    clock.advance(10.0)
    assert breaker.try_alert() is False

    # Cooldown elapses -> HALF_OPEN; record_success closes the breaker.
    clock.advance(60.0)
    breaker.record_success()
    assert breaker.state.consecutive_failures == 0
    assert breaker.state.opened_at is None

    # record_success resets the alert timer, so the recovery alert can fire.
    assert breaker.try_alert() is True
    assert breaker.state.last_alert_at == 70.0


# --- Persistence --------------------------------------------------------------


def test_state_persists_across_reinstantiation(tmp_path: Path):
    """Re-creating a CircuitBreaker with the same path reloads the persisted state."""

    breaker, clock, path = _make_breaker(
        tmp_path, threshold=3, cooldown=60.0, start=10.0
    )
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()  # opens at t=10
    assert breaker.try_alert() is True  # last_alert_at=10

    # Re-instantiate against the same path; clock at a later "now" so we can
    # tell that opened_at/last_alert_at were loaded from disk.
    clock.advance(5.0)  # t=15
    reloaded, _clock2, _path2 = _make_breaker(
        tmp_path, threshold=3, cooldown=60.0, start=15.0
    )
    state = reloaded.state
    assert state.consecutive_failures == 3
    assert state.opened_at == 10.0
    assert state.last_alert_at == 10.0
    assert reloaded.should_attempt() is False  # still in OPEN / cooldown


def test_corrupted_state_file_degrades_to_defaults(tmp_path: Path):
    """A non-JSON state file degrades to a fresh CLOSED breaker."""

    path = tmp_path / "breaker.json"
    path.write_text("{not valid json at all", encoding="utf-8")
    config = BreakerConfig(state_path=str(path))
    breaker = CircuitBreaker(config, clock=_FakeClock())
    state = breaker.state
    assert state.consecutive_failures == 0
    assert state.opened_at is None
    assert state.last_alert_at is None
    assert breaker.should_attempt() is True


def test_missing_state_file_degrades_to_defaults(tmp_path: Path):
    """A missing state file degrades to a fresh CLOSED breaker."""

    config = BreakerConfig(state_path=str(tmp_path / "missing.json"))
    breaker = CircuitBreaker(config, clock=_FakeClock())
    assert breaker.state.consecutive_failures == 0
    assert breaker.should_attempt() is True


def test_state_file_is_written_with_0600_permissions(tmp_path: Path):
    """The persisted state file is tightened to 0600 (mirrors SeenCache)."""

    breaker, _clock, path = _make_breaker(tmp_path, threshold=2)
    breaker.record_failure()
    breaker.record_failure()  # forces a save
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_state_file_is_loaded_with_0600_permissions_enforced(tmp_path: Path):
    """A world-readable state file is tightened to 0600 on load."""

    path = tmp_path / "breaker.json"
    path.write_text(json.dumps({"consecutive_failures": 0}), encoding="utf-8")
    os.chmod(path, 0o644)

    config = BreakerConfig(state_path=str(path))
    CircuitBreaker(config, clock=_FakeClock())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_atomic_write_leaves_no_tmp_file_on_disk(tmp_path: Path):
    """No ``.tmp.*`` files should remain after a state write."""

    breaker, _clock, path = _make_breaker(tmp_path, threshold=2)
    breaker.record_failure()
    breaker.record_failure()

    leftovers = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == []
    assert path.exists()


def test_atomic_write_does_not_leave_partial_file_when_rename_fails(
    tmp_path: Path, monkeypatch
):
    """If os.replace fails, the previous good state must remain readable."""

    breaker, _clock, path = _make_breaker(tmp_path, threshold=2)
    breaker.record_failure()  # writes a clean state file
    original = path.read_text(encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise OSError("rename failed")

    monkeypatch.setattr("jobtrail_ai_scorer._atomic_json.os.replace", _boom)

    # ``record_failure`` must raise, but the existing state file is untouched.
    with pytest.raises(OSError):
        breaker.record_failure()
    assert path.read_text(encoding="utf-8") == original


def test_state_file_creates_parent_directory_if_missing(tmp_path: Path):
    """The state file's parent directory is created on first write."""

    nested = tmp_path / "logs" / "automated-job-search" / "breaker.json"
    config = BreakerConfig(state_path=str(nested))
    breaker = CircuitBreaker(config, clock=_FakeClock())
    breaker.record_failure()  # forces a save under the nested path
    assert nested.exists()
    assert stat.S_IMODE(nested.stat().st_mode) == 0o600


def test_reloaded_state_handles_partial_payload(tmp_path: Path):
    """Missing/typed-wrong fields fall back to safe defaults."""

    path = tmp_path / "breaker.json"
    path.write_text(
        json.dumps(
            {
                "consecutive_failures": "not-a-number",
                "opened_at": "not-a-float",
                "last_alert_at": None,
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    config = BreakerConfig(state_path=str(path))
    breaker = CircuitBreaker(config, clock=_FakeClock())
    state = breaker.state
    assert state.consecutive_failures == 0
    assert state.opened_at is None
    assert state.last_alert_at is None