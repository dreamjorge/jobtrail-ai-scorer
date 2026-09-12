import json

import pytest

from jobtrail_ai_scorer.feedback import (
    FEEDBACK_MARKER,
    feedback_note_body,
    latest_feedback_for_job,
    parse_feedback_note,
    validate_labels,
)


def test_labels_are_closed_deduplicated_and_canonical():
    assert validate_labels(["other", "good_match", "other"]) == ("good_match", "other")


def test_feedback_note_is_bounded_and_private():
    body = feedback_note_body(["good_match"], comment="  useful  ", job={"sourceName": "indeed"})
    payload = parse_feedback_note(body)
    assert payload["source"] == "indeed"
    assert payload["comment"] == "useful"
    assert "description" not in body and "prompt" not in body


def test_latest_valid_feedback_ignores_malformed_notes():
    valid = feedback_note_body(["missing_skill"])
    malformed = FEEDBACK_MARKER + "\n" + json.dumps({"labels": ["not-valid"]})
    assert latest_feedback_for_job({"notes": [{"body": valid}, {"body": malformed}]})["labels"] == ["missing_skill"]


def test_invalid_labels_and_unbounded_comments_rejected():
    with pytest.raises(ValueError):
        validate_labels(["invalid"])
    with pytest.raises(ValueError):
        feedback_note_body(["other"], comment="x" * 501)


def test_feedback_metadata_is_normalized_and_bounded():
    payload = parse_feedback_note(feedback_note_body(["other"], job={"source": "  Indeed  "}))
    assert payload["source"] == "Indeed"
    with pytest.raises(ValueError):
        feedback_note_body(["other"], job={"source": "x" * 101})
    with pytest.raises(ValueError):
        feedback_note_body(["other"], job={"source": "bad\nsource"})
    from jobtrail_ai_scorer.feedback import build_feedback_payload
    with pytest.raises(ValueError):
        build_feedback_payload(["other"], scorer_version="")


@pytest.mark.parametrize("timestamp", [
    "2025-01-01T00:00:00+00:00",
    "2025-01-01T00:00:00-05:00",
    "2025-01-01 00:00:00Z",
])
def test_feedback_timestamp_must_be_canonical_utc(timestamp):
    from jobtrail_ai_scorer.feedback import build_feedback_payload
    with pytest.raises(ValueError):
        build_feedback_payload(["other"], timestamp=timestamp)


def test_feedback_accepts_canonical_utc_timestamp():
    from jobtrail_ai_scorer.feedback import build_feedback_payload
    assert build_feedback_payload(["other"], timestamp="2025-01-01T00:00:00Z")["timestamp"] == "2025-01-01T00:00:00Z"
