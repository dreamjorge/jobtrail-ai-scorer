"""Tests for the seen-cache TTL layer that dedups pre-import offers."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from jobtrail_ai_scorer.seen_cache import (
    DEFAULT_SEEN_CACHE_PATH,
    SEEN_CACHE_KEY_SEPARATOR,
    SeenCache,
)


class _FakeClock:
    """Deterministic clock for cache TTL tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- Constants ---------------------------------------------------------------


def test_default_seen_cache_path_is_outside_repo_and_under_logs():
    """The default path must be the runtime logs file outside the repo."""

    assert DEFAULT_SEEN_CACHE_PATH == (
        "/DATA/AppData/jobtrail/logs/automated-job-search/seen.json"
    )
    # Must not live inside the repository tree.
    assert "jobtrail-ai-scorer" not in DEFAULT_SEEN_CACHE_PATH
    # Must be under the documented runtime logs directory.
    assert DEFAULT_SEEN_CACHE_PATH.endswith("/automated-job-search/seen.json")


def test_seen_cache_key_separator_is_not_allowed_in_components():
    """The separator must be reserved so callers cannot forge collisions."""

    assert ":" not in SEEN_CACHE_KEY_SEPARATOR or len(SEEN_CACHE_KEY_SEPARATOR) == 1


# --- Empty cache --------------------------------------------------------------


def test_empty_cache_never_skips(tmp_path: Path):
    cache = SeenCache(tmp_path / "seen.json", clock=_FakeClock(1_000.0))
    assert cache.should_skip("linkedin", "job-1", hours_old=72) is False
    assert cache.size == 0


def test_corrupt_json_file_degrades_to_empty_cache(tmp_path: Path):
    path = tmp_path / "seen.json"
    path.write_text("{not valid json at all", encoding="utf-8")
    cache = SeenCache(path, clock=_FakeClock(1_000.0))
    assert cache.size == 0
    # After corruption we can still mark and skip.
    cache.mark_seen("linkedin", "recovered")
    assert cache.should_skip("linkedin", "recovered", hours_old=72) is True


def test_non_dict_payload_degrades_to_empty_cache(tmp_path: Path):
    path = tmp_path / "seen.json"
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    cache = SeenCache(path, clock=_FakeClock(1_000.0))
    assert cache.size == 0


def test_invalid_entry_shape_is_dropped_but_valid_entries_survive(tmp_path: Path):
    sep = SEEN_CACHE_KEY_SEPARATOR
    path = tmp_path / "seen.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    f"linkedin{sep}good": {"first_seen": 1_000.0},
                    f"linkedin{sep}bad-no-first-seen": {"oops": True},
                    f"linkedin{sep}bad-first-seen-type": {"first_seen": "not-a-number"},
                    f"linkedin{sep}bad-value": 42,
                },
            }
        ),
        encoding="utf-8",
    )
    cache = SeenCache(path, clock=_FakeClock(1_000.0))
    assert cache.size == 1
    assert cache.should_skip("linkedin", "good", hours_old=72) is True


# --- Hit / Miss ---------------------------------------------------------------


def test_mark_then_query_within_ttl_is_a_hit(tmp_path: Path):
    clock = _FakeClock(1_000.0)
    cache = SeenCache(tmp_path / "seen.json", clock=clock)
    cache.mark_seen("linkedin", "job-1")
    # Same instant: still in TTL.
    assert cache.should_skip("linkedin", "job-1", hours_old=72) is True
    # 1 hour later, well within 72*2 = 144h TTL.
    clock.advance(60 * 60)
    assert cache.should_skip("linkedin", "job-1", hours_old=72) is True


def test_mark_then_query_outside_ttl_is_a_miss(tmp_path: Path):
    clock = _FakeClock(1_000.0)
    cache = SeenCache(tmp_path / "seen.json", clock=clock)
    cache.mark_seen("linkedin", "job-1")
    # TTL is hours_old * 2 = 144 hours. Go past it.
    clock.advance(72 * 2 * 3600 + 60)
    assert cache.should_skip("linkedin", "job-1", hours_old=72) is False


def test_distinct_source_or_id_are_independent(tmp_path: Path):
    cache = SeenCache(tmp_path / "seen.json", clock=_FakeClock(1_000.0))
    cache.mark_seen("linkedin", "job-1")
    assert cache.should_skip("linkedin", "job-1", hours_old=72) is True
    # Different source, same id → miss.
    assert cache.should_skip("indeed", "job-1", hours_old=72) is False
    # Same source, different id → miss.
    assert cache.should_skip("linkedin", "job-2", hours_old=72) is False


def test_hours_old_parameter_changes_ttl(tmp_path: Path):
    clock = _FakeClock(1_000.0)
    cache = SeenCache(tmp_path / "seen.json", clock=clock)
    cache.mark_seen("linkedin", "job-1")
    # After 10 hours: hours_old=5 → TTL=10h → at boundary, expired.
    clock.advance(10 * 3600)
    assert cache.should_skip("linkedin", "job-1", hours_old=5) is False
    # Same time, hours_old=72 → TTL=144h → fresh.
    assert cache.should_skip("linkedin", "job-1", hours_old=72) is True


def test_mark_seen_refreshes_expired_entry(tmp_path: Path):
    """Re-marking an already-expired pair must restart its TTL window.

    ``mark_seen`` is only ever called by the automation after
    ``should_skip`` returned False, i.e. the pair is either new or its TTL
    already expired. If ``first_seen`` were preserved instead of refreshed,
    an expired entry would stay expired forever and the offer would get
    reimported/rescored on every subsequent run indefinitely.
    """

    sep = SEEN_CACHE_KEY_SEPARATOR
    clock = _FakeClock(1_000.0)
    cache = SeenCache(tmp_path / "seen.json", clock=clock)
    cache.mark_seen("linkedin", "job-1")
    # Move past the TTL (hours_old=72 -> 144h window).
    clock.advance(144 * 3600 + 60)
    assert cache.should_skip("linkedin", "job-1", hours_old=72) is False
    # Re-mark after expiry: first_seen must move to "now", not stay at 1000.0.
    cache.mark_seen("linkedin", "job-1")
    assert cache._entries[f"linkedin{sep}job-1"]["first_seen"] == clock()  # noqa: SLF001
    # The entry is fresh again relative to the new first_seen.
    assert cache.should_skip("linkedin", "job-1", hours_old=72) is True


# --- Persistence & atomic write ----------------------------------------------


def test_reload_preserves_entries_across_instances(tmp_path: Path):
    path = tmp_path / "seen.json"
    cache = SeenCache(path, clock=_FakeClock(1_000.0))
    cache.mark_seen("linkedin", "job-1")
    cache.mark_seen("indeed", "job-2")
    # New instance reads the same file.
    reloaded = SeenCache(path, clock=_FakeClock(1_010.0))
    assert reloaded.size == 2
    assert reloaded.should_skip("linkedin", "job-1", hours_old=72) is True
    assert reloaded.should_skip("indeed", "job-2", hours_old=72) is True


def test_atomic_write_leaves_no_tmp_file_on_disk(tmp_path: Path):
    """The cache must use tmp+rename; no .tmp files should remain after writes."""

    path = tmp_path / "seen.json"
    cache = SeenCache(path, clock=_FakeClock(1_000.0))
    cache.mark_seen("linkedin", "job-1")
    # Walk the directory; nothing should end with the tmp suffix.
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    # The actual file exists.
    assert path.exists()


def test_atomic_write_does_not_leave_partial_file_when_rename_fails(
    tmp_path: Path, monkeypatch
):
    """If rename fails, the previous good cache must remain readable."""

    path = tmp_path / "seen.json"
    cache = SeenCache(path, clock=_FakeClock(1_000.0))
    cache.mark_seen("linkedin", "job-1")
    original_contents = path.read_text(encoding="utf-8")

    # Force os.replace to fail so we exercise the failure branch.
    def _boom(*_args, **_kwargs):
        raise OSError("rename failed")

    monkeypatch.setattr("jobtrail_ai_scorer.seen_cache.os.replace", _boom)
    # The mark_seen call must raise but the previous file must be untouched.
    with pytest.raises(OSError):
        cache.mark_seen("linkedin", "job-2")
    assert path.read_text(encoding="utf-8") == original_contents


# --- Permissions --------------------------------------------------------------


def test_cache_file_is_created_with_0600_permissions(tmp_path: Path):
    path = tmp_path / "seen.json"
    cache = SeenCache(path, clock=_FakeClock(1_000.0))
    cache.mark_seen("linkedin", "job-1")
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_existing_cache_file_is_repaired_to_0600_when_loaded(tmp_path: Path):
    """If the file is left world-readable, the loader must tighten perms."""

    path = tmp_path / "seen.json"
    path.write_text(json.dumps({"version": 1, "entries": {}}), encoding="utf-8")
    os.chmod(path, 0o644)
    # Loading the cache should detect the loose perms and tighten them to 0600.
    SeenCache(path, clock=_FakeClock(1_000.0))
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


# --- Reset / bypass -----------------------------------------------------------


def test_reset_clears_all_entries(tmp_path: Path):
    path = tmp_path / "seen.json"
    cache = SeenCache(path, clock=_FakeClock(1_000.0))
    cache.mark_seen("linkedin", "job-1")
    cache.mark_seen("indeed", "job-2")
    assert cache.size == 2
    cache.reset()
    assert cache.size == 0
    # Re-read to confirm reset is persisted on disk.
    reloaded = SeenCache(path, clock=_FakeClock(1_010.0))
    assert reloaded.size == 0


# --- Reload against an unreadable file ----------------------------------------


def test_load_failure_does_not_propagate_and_cache_starts_empty(tmp_path: Path):
    path = tmp_path / "seen.json"
    # Directory exists but file does not; load should treat as empty (no exception).
    cache = SeenCache(path, clock=_FakeClock(1_000.0))
    assert cache.size == 0


def test_mark_seen_writes_even_when_parent_directory_must_be_created(tmp_path: Path):
    nested = tmp_path / "logs" / "automated-job-search" / "seen.json"
    cache = SeenCache(nested, clock=_FakeClock(1_000.0))
    cache.mark_seen("linkedin", "job-1")
    assert nested.exists()
    assert stat.S_IMODE(nested.stat().st_mode) == 0o600
    # Reload from the same nested path succeeds.
    reloaded = SeenCache(nested, clock=_FakeClock(1_010.0))
    assert reloaded.size == 1