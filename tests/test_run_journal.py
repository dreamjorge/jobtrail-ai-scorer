from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from jobtrail_ai_scorer.automation import AutomationRun
from jobtrail_ai_scorer.run_journal import (
    _line_for_run,
    _delivery_line,
    iter_runs,
    record_delivery,
    record_run,
)


def test_record_run_and_delivery_default_lock_preserves_concurrent_records(tmp_path):
    path = tmp_path / "runs.jsonl"
    result = SimpleNamespace(status="sent", classification="success", attempts=1)

    def write(index):
        if index % 2:
            record_run(
                path,
                AutomationRun(run_id=f"run-{index}"),
                started_at=float(index),
                finished_at=float(index + 1),
                base_url_source="test",
            )
        else:
            record_delivery(
                path,
                run_id=f"run-{index}",
                event_id=f"event-{index}",
                result=result,
            )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(20)))

    records = list(iter_runs(path))
    assert len(records) == 20
    assert {record["run_id"] for record in records} == {f"run-{i}" for i in range(20)}


def test_record_run_uses_established_automation_run_id(tmp_path):
    path = tmp_path / "runs.jsonl"
    run = AutomationRun(run_id="run-established")
    record_run(path, run, started_at=0.0, finished_at=1.0, base_url_source="test")
    assert next(iter(__import__("jobtrail_ai_scorer.run_journal", fromlist=["iter_runs"]).iter_runs(path)))["run_id"] == "run-established"


def test_delivery_line_links_run_event_and_classification_without_secrets():
    line = _delivery_line(
        run_id="run-1", event_id="event-1", result=SimpleNamespace(
            status="failed", classification="uncertain", attempts=3
        )
    )
    assert line == {
        "schema_version": 1,
        "record_type": "delivery",
        "run_id": "run-1",
        "event_id": "event-1",
        "status": "failed",
        "classification": "uncertain",
        "attempts": 3,
    }
    assert "secret" not in str(line).lower()


def test_sent_clean_no_match_is_classified_as_no_match():
    run = SimpleNamespace(
        searched=16,
        imported=2,
        scored=2,
        selected=None,
        failures=(),
        profile_counts={},
        notification_sent=True,
    )

    line = _line_for_run(
        run,
        started_at=0.0,
        finished_at=1.0,
        base_url_source="test",
        clock=None,
    )

    assert line["notified"] is True
    assert line["notification_kind"] == "no_match"


def test_legacy_run_without_notification_sent_uses_historical_fallback():
    run = SimpleNamespace(
        searched=3,
        imported=1,
        scored=1,
        selected={"title": "Selected"},
        failures=(),
        profile_counts={},
    )

    line = _line_for_run(
        run,
        started_at=0.0,
        finished_at=1.0,
        base_url_source="test",
        clock=None,
    )

    assert line["notified"] is True


def test_automation_run_default_notification_sent_falls_back_for_selected_or_failure():
    selected_run = AutomationRun(selected={"title": "Selected"})
    failure_run = AutomationRun(failures=("score:failed",))

    assert selected_run.notification_sent is None
    assert failure_run.notification_sent is None
    for run in (selected_run, failure_run):
        line = _line_for_run(
            run,
            started_at=0.0,
            finished_at=1.0,
            base_url_source="test",
            clock=None,
        )
        assert line["notified"] is True


def test_planned_run_without_sent_notification_remains_unclassified():
    run = SimpleNamespace(
        searched=3,
        imported=0,
        scored=0,
        selected=None,
        failures=(),
        profile_counts={},
        notification_sent=False,
    )

    line = _line_for_run(
        run,
        started_at=0.0,
        finished_at=1.0,
        base_url_source="test",
        clock=None,
    )

    assert line["notified"] is False
    assert line["notification_kind"] == "none"
