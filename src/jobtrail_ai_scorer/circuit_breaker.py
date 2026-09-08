"""Persistent circuit breaker for repeated upstream failures.

The breaker lives outside the repository at
``/DATA/AppData/jobtrail/logs/automated-job-search/breaker.json`` and uses the
same atomic-JSON contract as :class:`jobtrail_ai_scorer.seen_cache.SeenCache`
so concurrent or overlapping automation runs cannot corrupt the state file.

State machine
=============

::

    CLOSED ── consecutive_failures >= failure_threshold ──▶ OPEN
                                                            │
                                                cooldown_seconds elapsed
                                                            │
                                                            ▼
                                                       HALF_OPEN
                                                       │       │
                                                  success   failure
                                                       │       │
                                                       ▼       ▼
                                                    CLOSED   OPEN
                                                  (counter   (opened_at
                                                   reset;    = now; no
                                                   alert     auto-alert
                                                   timer     until
                                                   reset)    alert_cooldown
                                                             elapses)

* :meth:`CircuitBreaker.should_attempt` is ``True`` in CLOSED, and in
  HALF_OPEN (cooldown elapsed since ``opened_at``); ``False`` otherwise.
* :meth:`CircuitBreaker.record_failure` increments the counter, opens the
  breaker at the threshold, and updates ``opened_at`` to "now" each time
  the breaker is (re)opened. It never fires an alert on its own —
  alerting is the caller's responsibility via :meth:`CircuitBreaker.try_alert`.
* :meth:`CircuitBreaker.record_success` resets the breaker to CLOSED
  (``consecutive_failures=0``, ``opened_at=None``) **and** resets the
  alert timer so the recovery alert can fire once.
* :meth:`CircuitBreaker.try_alert` returns ``True`` iff no alert has
  been emitted within ``alert_cooldown_seconds``; on ``True`` it stamps
  ``last_alert_at`` and persists so subsequent calls within the cooldown
  return ``False``.

A corrupt or missing state file degrades to a fresh CLOSED breaker so
storage failures never crash the calling automation.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ._atomic_json import read_json, write_json_atomic


# Default path mirrors SeenCache's runtime logs location so both files
# share the same operator-managed parent directory.
DEFAULT_BREAKER_STATE_PATH = (
    "/DATA/AppData/jobtrail/logs/automated-job-search/breaker.json"
)

# Schema version persisted alongside the state. Bump when the on-disk
# shape changes in a non-backward-compatible way.
BREAKER_SCHEMA_VERSION = 1


# --- Configuration & state ---------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BreakerConfig:
    """Configuration for a :class:`CircuitBreaker`.

    Defaults match the design: three consecutive failures open the
    breaker, and both the open cooldown and the alert cooldown are one
    hour. ``state_path`` defaults to the runtime logs location shared
    with :class:`SeenCache`.
    """

    failure_threshold: int = 3
    cooldown_seconds: float = 3600.0
    alert_cooldown_seconds: float = 3600.0
    state_path: str = DEFAULT_BREAKER_STATE_PATH


@dataclasses.dataclass(frozen=True)
class BreakerState:
    """Persisted breaker state.

    ``consecutive_failures`` counts up to and beyond
    :attr:`BreakerConfig.failure_threshold` so the operator can see how
    badly a sustained outage is going. ``opened_at`` is the wall-clock
    timestamp (per the supplied clock) at which the breaker most
    recently transitioned to OPEN; ``None`` while CLOSED.
    ``last_alert_at`` is the timestamp of the most recent successful
    :meth:`CircuitBreaker.try_alert` call.
    """

    consecutive_failures: int = 0
    opened_at: float | None = None
    last_alert_at: float | None = None
    schema_version: int = BREAKER_SCHEMA_VERSION


# --- Breaker -----------------------------------------------------------------


class CircuitBreaker:
    """Persistent circuit breaker.

    The breaker's state is loaded from ``config.state_path`` on
    construction and re-persisted after every :meth:`record_failure`,
    :meth:`record_success`, and :meth:`try_alert` call. The clock is
    injectable for deterministic tests; the default uses
    :func:`time.time`.
    """

    def __init__(
        self,
        config: BreakerConfig,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._clock: Callable[[], float] = clock if clock is not None else time.time
        self._state: BreakerState = self._load()

    # --- public API ----------------------------------------------------------

    @property
    def state(self) -> BreakerState:
        """Return the current :class:`BreakerState` (frozen copy)."""

        return self._state

    @property
    def config(self) -> BreakerConfig:
        """Return the :class:`BreakerConfig` this breaker was built with."""

        return self._config

    def should_attempt(self) -> bool:
        """Return ``True`` iff an upstream call should be attempted now.

        CLOSED — always ``True``. OPEN — ``True`` only once
        ``now - opened_at >= cooldown_seconds`` (HALF_OPEN). The
        ``opened_at is None`` branch is a safety net for a malformed
        state where ``consecutive_failures`` reached the threshold
        without an ``opened_at`` ever being stamped: in that case we
        err on the side of attempting rather than blocking forever.
        """

        if self._state.consecutive_failures < self._config.failure_threshold:
            return True
        opened_at = self._state.opened_at
        if opened_at is None:
            return True
        return (self._clock() - opened_at) >= self._config.cooldown_seconds

    def record_failure(self) -> None:
        """Record a failure: increment the counter, open if at threshold.

        ``opened_at`` is updated to "now" on every call that happens
        while the breaker is at or past the threshold so the cooldown
        window restarts on each failure (the caller is still seeing
        failures, so the breaker should stay OPEN longer). ``last_alert_at``
        is never touched — alerting is the caller's job via
        :meth:`try_alert`.
        """

        new_count = self._state.consecutive_failures + 1
        opened_at = self._state.opened_at
        if new_count >= self._config.failure_threshold:
            # Restarts the cooldown on every failure once the breaker is
            # already OPEN, matching the "stay open while failures keep
            # coming" semantic.
            opened_at = self._clock()
        self._state = dataclasses.replace(
            self._state,
            consecutive_failures=new_count,
            opened_at=opened_at,
        )
        self._save()

    def record_success(self) -> None:
        """Record a success: close the breaker and reset the alert timer.

        ``last_alert_at`` is reset so the recovery alert (fired via
        :meth:`try_alert` after the breaker has been open for an outage
        and then recovers) is allowed to fire exactly once. ``opened_at``
        is cleared so the breaker is unambiguously CLOSED.
        """

        self._state = BreakerState(
            consecutive_failures=0,
            opened_at=None,
            last_alert_at=None,
            schema_version=self._state.schema_version,
        )
        self._save()

    def try_alert(self) -> bool:
        """Return ``True`` iff an alert should be emitted right now.

        On ``True``, ``last_alert_at`` is stamped with the current
        clock value and persisted so subsequent calls within
        ``alert_cooldown_seconds`` return ``False``. The function never
        raises on storage failure — alerting is best-effort and a
        failed persistence must not crash the automation.
        """

        now = self._clock()
        last = self._state.last_alert_at
        if last is not None and (now - last) < self._config.alert_cooldown_seconds:
            return False
        self._state = dataclasses.replace(self._state, last_alert_at=now)
        self._save()
        return True

    # --- persistence ---------------------------------------------------------

    def _save(self) -> None:
        """Persist ``self._state`` to ``config.state_path`` atomically."""

        payload = dataclasses.asdict(self._state)
        write_json_atomic(self._config.state_path, payload)

    def _load(self) -> BreakerState:
        """Load the breaker state from disk; degrade to defaults on failure."""

        path = Path(self._config.state_path)
        raw = read_json(path, default=None)
        if raw is None:
            return BreakerState()
        return _validate_state(raw)


# --- Validation --------------------------------------------------------------


def _validate_state(raw: Any) -> BreakerState:
    """Coerce a decoded-JSON payload into a :class:`BreakerState`.

    Bad fields fall back to safe defaults so a malformed state file
    cannot promote an automation run into a permanently-open breaker.
    ``bool`` is rejected for the numeric fields (Python treats ``True``
    as ``1`` and ``False`` as ``0``) so an attacker who can write JSON
    cannot silently bypass the failure threshold.
    """

    if not isinstance(raw, Mapping):
        return BreakerState()

    consecutive = raw.get("consecutive_failures", 0)
    opened = raw.get("opened_at")
    last_alert = raw.get("last_alert_at")

    return BreakerState(
        consecutive_failures=_coerce_int(consecutive, default=0),
        opened_at=_coerce_optional_float(opened),
        last_alert_at=_coerce_optional_float(last_alert),
        schema_version=BREAKER_SCHEMA_VERSION,
    )


def _coerce_int(value: Any, *, default: int) -> int:
    """Return ``value`` as an ``int``; reject ``bool``; fall back to ``default``."""

    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return int(value)
    return default


def _coerce_optional_float(value: Any) -> float | None:
    """Return ``value`` as a ``float`` or ``None``; reject ``bool``."""

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


__all__ = [
    "BREAKER_SCHEMA_VERSION",
    "DEFAULT_BREAKER_STATE_PATH",
    "BreakerConfig",
    "BreakerState",
    "CircuitBreaker",
]