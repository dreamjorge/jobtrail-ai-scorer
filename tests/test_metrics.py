from datetime import datetime, timezone

import pytest

from jobtrail_ai_scorer.metrics import compute_metrics
from jobtrail_ai_scorer.lifecycle import make_lifecycle_event


def ts(day, hour=0):
    return datetime(2025, 1, day, hour, tzinfo=timezone.utc).timestamp()


def test_period_boundaries_and_journal_aggregation():
    view = compute_metrics(
        runs=[
            {"started_at": ts(7), "searched": 2, "imported": 1, "deduplicated": 1,
             "scored": 1, "changed": 3, "rescored": 2, "scored_failed": 0,
             "notified": True, "finished_at": ts(7, 1)},
            {"started_at": ts(1), "searched": 9, "finished_at": ts(1, 1)},
        ], period="7d", now=ts(8, 12), seen_entries=[{"source": "board", "first_seen": ts(7)}],
    )
    assert view.searched == 2
    assert view.imported == 1
    assert view.notifications == 1
    assert view.changed == 3
    assert view.rescored == 2
    assert view.to_dict()["changed"] == 3
    assert view.to_dict()["rescored"] == 2
    assert view.last_success_at == ts(7, 1)


def test_legacy_journal_lines_default_new_totals_to_zero():
    view = compute_metrics(
        runs=[{"started_at": ts(7), "searched": 1}],
        period="today",
        now=ts(7, 12),
    )
    assert view.changed == 0
    assert view.rescored == 0


def test_today_is_utc_and_rejects_invalid_period_or_future_now():
    assert compute_metrics(runs=[], period="today", now=ts(7, 12)).since == ts(7)
    with pytest.raises(ValueError):
        compute_metrics(runs=[], period="90d", now=ts(7))
    with pytest.raises(ValueError):
        compute_metrics(runs=[], period="today", now=-1)


def test_empty_and_missing_data_are_explicit():
    view = compute_metrics(runs=[], period="30d", now=ts(7), scored_jobs=[])
    assert {"searched", "source_counts", "deduplicated", "notifications",
            "last_success_at", "last_failure_at", "application_status"} <= set(view.missing_data)
    assert view.application_status == {}


def test_score_bins_edges_threshold_and_status():
    view = compute_metrics(
        runs=[], period="30d", now=ts(8), threshold=80,
        scored_jobs=[
            {"source": "a", "searchProfile": "p", "score": 0, "applicationStatus": "new", "created_at": ts(7)},
            {"source": "a", "profile": "p", "score": 9, "applicationStatus": "applied", "created_at": ts(7)},
            {"source": "b", "profile": "q", "score": 10, "applicationStatus": "new", "created_at": ts(7)},
            {"source": "b", "profile": "q", "score": 80, "applicationStatus": "rejected", "created_at": ts(7)},
            {"source": "b", "profile": "q", "score": 100, "applicationStatus": "new", "created_at": ts(7)},
        ],
    )
    assert view.score_bins["0-9"] == 2
    assert view.score_bins["10-19"] == 1
    assert view.score_bins["80-89"] == 1
    assert view.score_bins["90-100"] == 1
    assert view.matches_above_threshold == 2
    assert view.application_status == {"new": 3, "applied": 1, "rejected": 1}


def test_failure_notification_and_last_failure():
    view = compute_metrics(
        runs=[
            {"started_at": ts(6), "finished_at": ts(6, 1), "failures": ["score:x"], "notified": True},
            {"started_at": ts(7), "finished_at": ts(7, 1), "failures": ["search:x"], "notified": False},
        ], period="30d", now=ts(7, 12),
    )
    assert view.scored_failed == 1
    assert view.notifications == 1
    assert view.last_failure_at == ts(7, 1)


def test_source_profile_aggregation_and_redaction():
    view = compute_metrics(
        runs=[{"started_at": ts(7), "source_counts": {"board": {"searched": 2}},
               "profile_counts": {"senior": {"searched": 2}}}],
        seen_entries=[{"source": "board", "first_seen": ts(7)}, {"source": "board", "first_seen": ts(7)}],
        scored_jobs=[{"source": "board", "profile": "senior", "score": 81, "created_at": ts(7),
                      "description": "secret", "notes": [{"body": "secret"}],
                      "metadata": {"secret": 1}, "jobUrl": "https://secret"}],
        period="today", now=ts(7, 12),
    )
    assert view.source_stats["board"].imported == 2
    assert view.profile_stats["senior"].scored == 1
    raw = str(view.to_dict())
    assert all(secret not in raw for secret in ("secret", "https://secret"))


def test_lifecycle_counts_prefer_valid_notes_and_fallback_to_backend_status():
    note = make_lifecycle_event("new", "scored", source="test", source_job_id="j")
    view = compute_metrics(
        runs=[], period="today", now=ts(7),
        scored_jobs=[{"notes": [{"body": note}], "applicationStatus": "applied", "created_at": ts(7)}, {"applicationStatus": "rejected", "created_at": ts(7)}],
    )
    assert view.lifecycle_counts == {"rejected": 1, "scored": 1}
    assert view.application_status == {"applied": 1, "rejected": 1}


def test_lifecycle_counts_are_independent_of_missing_application_status():
    note = make_lifecycle_event("new", "scored", source="test", source_job_id="j")
    view = compute_metrics(
        runs=[], period="today", now=ts(7),
        scored_jobs=[{"notes": [{"body": note}], "created_at": ts(7)}],
    )
    assert view.lifecycle_counts == {"scored": 1}
    assert "lifecycle_counts" not in view.missing_data
    assert "application_status" in view.missing_data


def test_status_missing_when_jobs_have_no_safe_status():
    view = compute_metrics(runs=[], period="today", now=ts(7), scored_jobs=[{"score": 50}])
    assert view.application_status == {}
    assert "application_status" in view.missing_data


def test_jobtrail_camel_case_timestamps_filter_jobs_and_missing_fails_closed():
    now = ts(7, 12)
    view = compute_metrics(
        runs=[], period="30d", now=now,
        scored_jobs=[
            {"createdAt": "2025-01-07T01:00:00Z", "score": 80, "applicationStatus": "new"},
            {"updatedAt": "2024-12-01T01:00:00Z", "score": 90, "applicationStatus": "old"},
            {"score": 100, "applicationStatus": "unknown"},
        ],
    )
    assert view.score_bins["80-89"] == 1
    assert view.score_bins["90-100"] == 0
    assert view.application_status == {"new": 1}
