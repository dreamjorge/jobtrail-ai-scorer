"""Behavior tests for the append-only automation run journal.

These tests pin the public contract of
:mod:`jobtrail_ai_scorer.run_journal`:

* :data:`DEFAULT_RUN_JOURNAL_PATH` mirrors the other persistent runtime logs
  (``/DATA/AppData/jobtrail/logs/automated-job-search/runs.jsonl``).
* :data:`SCHEMA_VERSION` is the version embedded in every journal line.
* :func:`record_run` appends one immutable JSONL line with the funnel fields
  the design promises and never overwrites a previous line.
* :func:`iter_runs` reads lines back in order; the period filter is
  half-open ``[since, until)``; corrupted lines are skipped, never raised.
* Notification classification picks the highest-priority kind
  (``match`` > ``breaker`` > ``preflight`` > ``failure`` > ``none``) so the
  metric view never mis-classifies a breaker-opened run as a plain failure.
* The journal file is created with ``0600`` permissions and the parent
  directory is created on first write — matching the SeenCache contract.
"""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from jobtrail_ai_scorer.automation import AutomationRun
from jobtrail_ai_scorer.run_journal import (
    DEFAULT_RUN_JOURNAL_PATH,
    SCHEMA_VERSION,
    iter_runs,
    record_run,
)


# --- Constants -----------------------------------------------------------------


def test_default_run_journal_path_is_outside_repo_and_under_logs():
    """The default journal path mirrors SeenCache's runtime logs location."""

    assert DEFAULT_RUN_JOURNAL_PATH == (
        "/DATA/AppData/jobtrail/logs/automated-job-search/runs.jsonl"
    )
    assert "jobtrail-ai-scorer" not in DEFAULT_RUN_JOURNAL_PATH
    assert DEFAULT_RUN_JOURNAL_PATH.endswith("/automated-job-search/runs.jsonl")


def test_schema_version_is_one():
    """``SCHEMA_VERSION`` is the constant embedded in every journal line."""

    assert SCHEMA_VERSION == 1


# --- Helpers -------------------------------------------------------------------


_RUNID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-[a-f0-9]{6}$")


def _run(
    *,
    searched: int = 0,
    imported: int = 0,
    scored: int = 0,
    failures: tuple[str, ...] = (),
    profile_counts: dict[str, dict[str, int]] | None = None,
    selected: dict[str, Any] | None = None,
) -> AutomationRun:
    """Build a minimal :class:`AutomationRun` for journal tests."""

    return AutomationRun(
        searched=searched,
        imported=imported,
        scored=scored,
        failures=failures,
        selected=selected,
        profile_counts=profile_counts or {},
    )


def _parse_lines(path: Path) -> list[dict[str, Any]]:
    """Return the raw JSONL lines as decoded dicts (helper for assertions)."""

    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- record_run: shape of one line ---------------------------------------------


def test_record_run_appends_one_line_with_expected_fields(tmp_path: Path):
    """One record_run call writes exactly one line with the funnel fields."""

    path = tmp_path / "runs.jsonl"
    run = _run(searched=12, imported=8, scored=3)
    record_run(
        path,
        run,
        started_at=datetime(2026, 9, 8, 11, 30, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 11, 35, tzinfo=timezone.utc),
        base_url_source="static",
    )

    lines = _parse_lines(path)
    assert len(lines) == 1
    line = lines[0]
    assert line["schema_version"] == SCHEMA_VERSION
    assert isinstance(line["run_id"], str)
    assert _RUNID_PATTERN.match(line["run_id"])
    assert line["started_at"] == pytest.approx(
        datetime(2026, 9, 8, 11, 30, tzinfo=timezone.utc).timestamp()
    )
    assert line["finished_at"] == pytest.approx(
        datetime(2026, 9, 8, 11, 35, tzinfo=timezone.utc).timestamp()
    )
    assert line["searched"] == 12
    assert line["imported"] == 8
    assert line["deduplicated"] == 4  # searched - imported
    assert line["scored"] == 3
    assert line["scored_failed"] == 0
    assert line["notified"] is False  # no selection and no failures
    assert line["notification_kind"] == "none"
    assert line["failures"] == []
    assert line["base_url_source"] == "static"


def test_record_run_appending_twice_produces_two_distinct_lines(tmp_path: Path):
    """Two record_run calls produce two lines; the first is not overwritten."""

    path = tmp_path / "runs.jsonl"
    run_a = _run(searched=5, imported=3, scored=1)
    run_b = _run(searched=7, imported=4, scored=2)

    record_run(
        path,
        run_a,
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc),
        base_url_source="static",
    )
    record_run(
        path,
        run_b,
        started_at=datetime(2026, 9, 8, 11, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 11, 2, tzinfo=timezone.utc),
        base_url_source="discovery",
    )

    lines = _parse_lines(path)
    assert len(lines) == 2
    assert lines[0]["searched"] == 5
    assert lines[0]["imported"] == 3
    assert lines[0]["base_url_source"] == "static"
    assert lines[1]["searched"] == 7
    assert lines[1]["imported"] == 4
    assert lines[1]["base_url_source"] == "discovery"


def test_record_run_run_id_is_deterministic_for_same_started_at(tmp_path: Path):
    """The run_id is reproducible from started_at so callers can correlate."""

    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    started_at = datetime(2026, 9, 8, 11, 30, tzinfo=timezone.utc)
    finished_at = datetime(2026, 9, 8, 11, 31, tzinfo=timezone.utc)

    record_run(
        path_a, _run(),
        started_at=started_at, finished_at=finished_at,
        base_url_source="static",
    )
    record_run(
        path_b, _run(),
        started_at=started_at, finished_at=finished_at,
        base_url_source="static",
    )

    line_a = _parse_lines(path_a)[0]
    line_b = _parse_lines(path_b)[0]
    assert line_a["run_id"] == line_b["run_id"]


# --- iter_runs: ordering and period filter -------------------------------------


def test_iter_runs_returns_lines_in_order(tmp_path: Path):
    """iter_runs yields every well-formed line in the order they were written."""

    path = tmp_path / "runs.jsonl"
    base = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    for offset in range(3):
        record_run(
            path,
            _run(searched=offset + 1),
            started_at=base.replace(minute=offset),
            finished_at=base.replace(minute=offset, second=30),
            base_url_source="static",
        )

    lines = list(iter_runs(path))
    assert [line["searched"] for line in lines] == [1, 2, 3]


def test_iter_runs_period_filter_is_inclusive_since_exclusive_until(tmp_path: Path):
    """The period filter is half-open: ``[since, until)``."""

    path = tmp_path / "runs.jsonl"
    base = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    for minute in (0, 15, 30, 45):
        record_run(
            path,
            _run(),
            started_at=base.replace(minute=minute),
            finished_at=base.replace(minute=minute, second=30),
            base_url_source="static",
        )

    # since inclusive: the line at t=10:15 is included.
    since = base.replace(minute=15).timestamp()
    until = base.replace(minute=45).timestamp()
    inside = list(iter_runs(path, since=since, until=until))
    assert [line["started_at"] for line in inside] == [
        base.replace(minute=15).timestamp(),
        base.replace(minute=30).timestamp(),
    ]

    # No window: returns every line.
    everything = list(iter_runs(path))
    assert len(everything) == 4


def test_iter_runs_returns_empty_for_missing_file(tmp_path: Path):
    """A missing journal file yields no lines and does not raise."""

    path = tmp_path / "missing.jsonl"
    assert list(iter_runs(path)) == []


def test_iter_runs_skips_corrupted_lines_without_raising(tmp_path: Path):
    """Corrupted lines are dropped; well-formed lines are still returned."""

    path = tmp_path / "runs.jsonl"
    base = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    record_run(
        path,
        _run(searched=11),
        started_at=base,
        finished_at=base.replace(second=30),
        base_url_source="static",
    )
    # Inject a bad line in the middle and at the end.
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json at all\n")
    record_run(
        path,
        _run(searched=22),
        started_at=base.replace(minute=5),
        finished_at=base.replace(minute=5, second=30),
        base_url_source="static",
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    lines = list(iter_runs(path))
    assert [line["searched"] for line in lines] == [11, 22]


# --- Permissions / parent directory --------------------------------------------


def test_record_run_creates_file_with_0600_permissions(tmp_path: Path):
    """The journal file is tightened to 0600 on first write."""

    path = tmp_path / "runs.jsonl"
    record_run(
        path,
        _run(),
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 0, second=30, tzinfo=timezone.utc),
        base_url_source="static",
    )
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_record_run_creates_parent_directory_if_missing(tmp_path: Path):
    """A nested parent directory is created on first write."""

    nested = tmp_path / "logs" / "automated-job-search" / "runs.jsonl"
    assert not nested.parent.exists()
    record_run(
        nested,
        _run(),
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 0, second=30, tzinfo=timezone.utc),
        base_url_source="static",
    )
    assert nested.exists()
    assert stat.S_IMODE(nested.stat().st_mode) == 0o600


# --- Notification classification -----------------------------------------------


def test_notification_kind_is_match_when_selected_present(tmp_path: Path):
    """A run with a selected match reports ``kind="match"`` and notified=True."""

    path = tmp_path / "runs.jsonl"
    run = _run(
        selected={"title": "Python Engineer", "score": 91, "recommendation": "APPLY"},
    )
    record_run(
        path, run,
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc),
        base_url_source="static",
    )
    line = _parse_lines(path)[0]
    assert line["notification_kind"] == "match"
    assert line["notified"] is True


def test_notification_kind_is_failure_when_only_failures(tmp_path: Path):
    """A run with failures and no selected match reports ``kind="failure"``."""

    path = tmp_path / "runs.jsonl"
    run = _run(failures=("score:job-1:terminal:ValueError",))
    record_run(
        path, run,
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc),
        base_url_source="static",
    )
    line = _parse_lines(path)[0]
    assert line["notification_kind"] == "failure"
    assert line["notified"] is True


def test_notification_kind_is_breaker_when_breaker_open(tmp_path: Path):
    """A run with ``breaker:open`` reports ``kind="breaker"`` (beats failure)."""

    path = tmp_path / "runs.jsonl"
    run = _run(failures=("breaker:open",))
    record_run(
        path, run,
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc),
        base_url_source="static",
    )
    line = _parse_lines(path)[0]
    assert line["notification_kind"] == "breaker"
    assert line["notified"] is True


def test_notification_kind_is_preflight_when_preflight_unavailable(tmp_path: Path):
    """A run with ``preflight:unavailable:*`` reports ``kind="preflight"``."""

    path = tmp_path / "runs.jsonl"
    run = _run(failures=("preflight:unavailable:backend",))
    record_run(
        path, run,
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc),
        base_url_source="static",
    )
    line = _parse_lines(path)[0]
    assert line["notification_kind"] == "preflight"
    assert line["notified"] is True


def test_notification_kind_is_match_when_match_and_failures_coexist(tmp_path: Path):
    """``match`` wins over ``failure`` so the metric view always picks the best."""

    path = tmp_path / "runs.jsonl"
    run = _run(
        selected={"title": "Python Engineer", "score": 91},
        failures=("score:job-2:terminal:ValueError",),
    )
    record_run(
        path, run,
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc),
        base_url_source="static",
    )
    line = _parse_lines(path)[0]
    assert line["notification_kind"] == "match"
    assert line["notified"] is True


def test_notification_kind_is_none_when_no_selection_no_failures(tmp_path: Path):
    """An empty run reports ``kind="none"`` and notified=False."""

    path = tmp_path / "runs.jsonl"
    record_run(
        path, _run(),
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc),
        base_url_source="static",
    )
    line = _parse_lines(path)[0]
    assert line["notification_kind"] == "none"
    assert line["notified"] is False


# --- scored_failed / deduplicated derivation ----------------------------------


def test_scored_failed_counts_score_stage_failures(tmp_path: Path):
    """``scored_failed`` is the count of failures whose stage is ``score``."""

    path = tmp_path / "runs.jsonl"
    run = _run(
        scored=2,
        failures=(
            "score:job-1:terminal:ValueError",
            "score:job-2:exhausted:TimeoutError",
            "import:job-3:retryable:RuntimeError",
        ),
    )
    record_run(
        path, run,
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc),
        base_url_source="static",
    )
    line = _parse_lines(path)[0]
    assert line["scored_failed"] == 2


def test_deduplicated_sums_profile_duplicates_when_profiles_present(tmp_path: Path):
    """``deduplicated`` sums per-profile duplicates when profile counts exist."""

    path = tmp_path / "runs.jsonl"
    run = _run(
        searched=10,
        imported=6,
        profile_counts={
            "alpha": {"searched": 5, "imported": 3, "duplicates": 2, "failures": 0},
            "beta": {"searched": 5, "imported": 3, "duplicates": 2, "failures": 0},
        },
    )
    record_run(
        path, run,
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc),
        base_url_source="static",
    )
    line = _parse_lines(path)[0]
    assert line["deduplicated"] == 4


def test_deduplicated_falls_back_to_searched_minus_imported(tmp_path: Path):
    """Without profiles, ``deduplicated`` is ``max(0, searched - imported)``."""

    path = tmp_path / "runs.jsonl"
    run = _run(searched=9, imported=12)  # unusual but bounded
    record_run(
        path, run,
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc),
        base_url_source="static",
    )
    line = _parse_lines(path)[0]
    assert line["deduplicated"] == 0  # clamped to non-negative


# --- Lock path plumbing -------------------------------------------------------


def test_record_run_honors_lock_path_for_cross_process_safety(tmp_path: Path):
    """When ``lock_path`` is provided, the write is wrapped in an advisory lock."""

    path = tmp_path / "runs.jsonl"
    lock = tmp_path / "runs.lock"
    record_run(
        path, _run(searched=1),
        started_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 8, 10, 0, second=30, tzinfo=timezone.utc),
        base_url_source="static",
        lock_path=lock,
    )
    # The lock file is created with 0600 (per the _atomic_json helper).
    assert lock.exists()
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert len(_parse_lines(path)) == 1