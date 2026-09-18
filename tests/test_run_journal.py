from types import SimpleNamespace

from jobtrail_ai_scorer.automation import AutomationRun
from jobtrail_ai_scorer.run_journal import _line_for_run, _delivery_line


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
