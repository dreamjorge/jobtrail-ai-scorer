"""Focused contract tests for the append-only automation run journal."""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from jobtrail_ai_scorer.automation import AutomationRun
from jobtrail_ai_scorer.run_journal import SCHEMA_VERSION, iter_runs, record_run


BASE = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)


def _run(
    *,
    searched: int = 0,
    imported: int = 0,
    scored: int = 0,
    failures: tuple[str, ...] = (),
    profile_counts: dict[str, dict[str, int]] | None = None,
    selected: dict[str, Any] | None = None,
) -> AutomationRun:
    return AutomationRun(
        searched=searched,
        imported=imported,
        scored=scored,
        failures=failures,
        selected=selected,
        profile_counts=profile_counts or {},
    )


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _record(path: Path, run: AutomationRun, minute: int = 0, **kwargs: Any) -> None:
    started = BASE.replace(minute=minute)
    record_run(
        path,
        run,
        started_at=started,
        finished_at=started.replace(second=30),
        base_url_source=kwargs.pop("base_url_source", "static"),
        **kwargs,
    )


def test_append_has_expected_schema_and_derived_fields(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    _record(path, _run(searched=12, imported=8, scored=3))

    line = _lines(path)[0]
    assert set(line) == {
        "schema_version", "run_id", "started_at", "finished_at", "searched",
        "imported", "deduplicated", "scored", "scored_failed", "notified",
        "notification_kind", "failures", "base_url_source",
    }
    assert line["schema_version"] == SCHEMA_VERSION
    assert isinstance(line["run_id"], str)
    assert line["started_at"] == BASE.timestamp()
    assert line["finished_at"] == BASE.replace(second=30).timestamp()
    assert line["searched"] == 12
    assert line["imported"] == 8
    assert line["deduplicated"] == 4
    assert line["scored"] == 3
    assert line["scored_failed"] == 0
    assert line["notified"] is False
    assert line["notification_kind"] == "none"
    assert line["failures"] == []
    assert line["base_url_source"] == "static"


def test_two_appends_remain_two_jsonl_records(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    _record(path, _run(searched=5, imported=3), minute=0)
    _record(path, _run(searched=7, imported=4), minute=1, base_url_source="discovery")

    lines = _lines(path)
    assert len(lines) == 2
    assert [(line["searched"], line["imported"]) for line in lines] == [(5, 3), (7, 4)]
    assert lines[1]["base_url_source"] == "discovery"


def test_iter_runs_filters_half_open_window_and_skips_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    for minute, searched in ((0, 10), (15, 15), (30, 30)):
        _record(path, _run(searched=searched), minute=minute)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    _record(path, _run(searched=45), minute=45)

    result = list(iter_runs(path, since=BASE.replace(minute=15).timestamp(), until=BASE.replace(minute=45).timestamp()))
    assert [line["searched"] for line in result] == [15, 30]
    assert len(list(iter_runs(path))) == 4


def test_append_creates_parent_and_restricts_file_to_0600(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "automated-job-search" / "runs.jsonl"
    _record(path, _run())

    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("selected", "failures", "kind"),
    [
        (None, (), "none"),
        (None, ("score:job:terminal:ValueError",), "failure"),
        (None, ("breaker:open",), "breaker"),
        (None, ("preflight:unavailable:backend",), "preflight"),
        ({"title": "Python Engineer", "score": 91}, (), "match"),
        ({"title": "Python Engineer"}, ("breaker:open",), "match"),
    ],
)
def test_notification_kind_classification(
    tmp_path: Path,
    selected: dict[str, Any] | None,
    failures: tuple[str, ...],
    kind: str,
) -> None:
    path = tmp_path / "runs.jsonl"
    _record(path, _run(selected=selected, failures=failures))

    line = _lines(path)[0]
    assert line["notification_kind"] == kind
    assert line["notified"] is (kind != "none")


@pytest.mark.parametrize(
    ("run", "scored_failed", "deduplicated"),
    [
        (
            _run(
                searched=10,
                imported=6,
                scored=2,
                failures=(
                    "score:job-1:terminal:ValueError",
                    "score:job-2:exhausted:TimeoutError",
                    "import:job-3:retryable:RuntimeError",
                ),
            ),
            2,
            4,
        ),
        (
            _run(
                searched=10,
                imported=6,
                profile_counts={
                    "alpha": {"searched": 5, "imported": 3, "duplicates": 2, "failures": 0},
                    "beta": {"searched": 5, "imported": 3, "duplicates": 2, "failures": 0},
                },
            ),
            0,
            4,
        ),
        (_run(searched=9, imported=12), 0, 0),
    ],
)
def test_scored_failed_and_deduplicated_derivation(
    tmp_path: Path, run: AutomationRun, scored_failed: int, deduplicated: int
) -> None:
    path = tmp_path / "runs.jsonl"
    _record(path, run)

    line = _lines(path)[0]
    assert line["scored_failed"] == scored_failed
    assert line["deduplicated"] == deduplicated
