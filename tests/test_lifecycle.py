from datetime import datetime, timezone

import pytest

from jobtrail_ai_scorer.lifecycle import (
    LIFECYCLE_MARKER, current_state, lifecycle_history, make_lifecycle_event,
    parse_lifecycle_note, serialize_lifecycle_event, validate_transition,
)


def stamp():
    return datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def test_transition_and_history_are_append_only():
    first = make_lifecycle_event("new", "scored", source="test", source_job_id="j", timestamp=stamp())
    second = make_lifecycle_event("scored", "reviewing", source="test", source_job_id="j", timestamp=stamp())
    job = {"notes": [{"body": first}, {"body": "garbage"}, {"body": second}]}
    assert current_state(job) == "reviewing"
    assert len(lifecycle_history(job)) == 2


def test_applied_requires_confirmation_and_is_not_directly_reachable():
    with pytest.raises(ValueError, match="confirmation"):
        validate_transition("prepared", "applied")
    with pytest.raises(ValueError, match="invalid"):
        validate_transition("new", "applied", confirm=True)
    assert parse_lifecycle_note(make_lifecycle_event("prepared", "applied", source="cli", source_job_id="j", confirm=True, timestamp=stamp()))["confirmed"] is True


def test_parser_rejects_noncanonical_and_private_provenance():
    event = make_lifecycle_event("new", "scored", source="test", source_job_id="j", timestamp=stamp())
    assert parse_lifecycle_note(event.replace("{", "{\"private\":1,", 1)) is None
    with pytest.raises(ValueError, match="disallowed"):
        serialize_lifecycle_event({"schema_version": 1, "timestamp": stamp(), "previous_state": "new", "new_state": "scored", "source": "x", "source_job_id": "j", "provenance": {"description": "secret"}})


    def test_provenance_accepts_position_alias_and_consistency_is_enforced():
        from jobtrail_ai_scorer.lifecycle import provenance_from_job

        provenance = provenance_from_job({"position": "Engineer", "source": "board", "sourceJobId": "remote-1"})
        assert provenance == {"title": "Engineer", "source": "board", "source_job_id": "remote-1"}
        with pytest.raises(ValueError, match="provenance.source"):
            make_lifecycle_event("new", "scored", source="other", source_job_id="remote-1", provenance=provenance)
        with pytest.raises(ValueError, match="provenance.source_job_id"):
            make_lifecycle_event("new", "scored", source="board", source_job_id="internal-1", provenance=provenance)


def test_current_state_falls_back_to_valid_backend_status_without_inferencing_unknown_values():
    assert current_state({"applicationStatus": "applied", "notes": []}) == "applied"
    assert current_state({"status": "interview", "notes": []}) == "interview"
    assert current_state({"applicationStatus": "unknown", "status": "not-a-state", "notes": []}) == "new"
