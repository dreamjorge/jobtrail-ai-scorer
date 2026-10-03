"""Pure parsing helpers for persisted score notes.

These tests pin ``parse_score_note`` to a behavior that has no dependency on
``JobSearchAutomation`` or any I/O boundary. The helper is the foundation for
the offline automation dry-run slice in #36: the simulation reuses it to
recover the latest valid score from captured scenario notes, never via a live
backend round-trip.

Each scenario is one assertion so a failing test points to exactly one
breaking behavior, following the project's strict TDD discipline.
"""

from __future__ import annotations

from typing import Any

import jobtrail_ai_scorer.scoring as scoring

import pytest

from jobtrail_ai_scorer.scoring import parse_score_note


VALID_MARKER = "[AI_JOB_SCORE_V1]"
LEGACY_MARKER = "[HERMES_JOB_SCORE_V1]"


def _build_body(score: Any, marker: str = VALID_MARKER) -> str:
    return f"{marker}\n{score}"


def _valid_payload(score: int, recommendation: str = "APPLY", reasoning: str = "ok") -> str:
    return (
        f'{{"score":{score},"recommendation":"{recommendation}",'
        f'"strengths":[],"gaps":[],"needs_confirmation":[],'
        f'"hard_requirements_missing":[],"career_value":"Medium",'
        f'"reasoning":"{reasoning}"}}'
    )


def test_returns_none_when_notes_is_not_a_list() -> None:
    assert parse_score_note(None) is None
    assert parse_score_note("not a list") is None
    assert parse_score_note({"body": "x"}) is None


def test_returns_none_when_notes_list_is_empty() -> None:
    assert parse_score_note([]) is None


def test_returns_none_when_no_marker_present() -> None:
    notes = [
        {"body": "some unrelated note"},
        {"body": "another note without the marker"},
    ]
    assert parse_score_note(notes) is None


def test_ignores_note_with_marker_but_non_string_body() -> None:
    notes = [{"body": 123}, {"body": None}, {"body": [VALID_MARKER]}]
    assert parse_score_note(notes) is None


def test_ignores_note_when_marker_is_present_but_payload_text_is_empty() -> None:
    notes = [{"body": f"{VALID_MARKER}\n   "}]
    assert parse_score_note(notes) is None


def test_returns_none_when_payload_is_not_valid_json() -> None:
    notes = [{"body": _build_body("not-json")}]
    assert parse_score_note(notes) is None


def test_returns_none_when_payload_is_not_an_object() -> None:
    notes = [{"body": _build_body("[1, 2, 3]")}]
    assert parse_score_note(notes) is None


def test_returns_none_when_payload_object_has_no_score() -> None:
    notes = [{"body": _build_body('{"recommendation":"APPLY"}')}]
    assert parse_score_note(notes) is None


def test_returns_none_when_score_is_not_a_number() -> None:
    notes = [{"body": _build_body('{"score":"80","recommendation":"APPLY"}')}]
    assert parse_score_note(notes) is None


def test_returns_none_when_score_is_a_boolean() -> None:
    notes = [{"body": _build_body('{"score":true,"recommendation":"APPLY"}')}]
    assert parse_score_note(notes) is None


def test_returns_none_when_score_is_out_of_range() -> None:
    notes = [{"body": _build_body(_valid_payload(250))}]
    assert parse_score_note(notes) is None
    notes = [{"body": _build_body(_valid_payload(-5))}]
    assert parse_score_note(notes) is None


def test_coerces_float_score_to_int() -> None:
    notes = [{"body": _build_body(_valid_payload(70.7))}]
    result = parse_score_note(notes)
    assert result is not None
    assert result["score"] == 70
    assert isinstance(result["score"], int)


def test_default_parser_accepts_legacy_marker() -> None:
    notes = [{"body": _build_body(_valid_payload(78), marker=LEGACY_MARKER)}]
    result = parse_score_note(notes)
    assert result is not None
    assert result["score"] == 78


@pytest.mark.parametrize("existing_marker", [VALID_MARKER, LEGACY_MARKER])
def test_custom_marker_does_not_enable_current_or_legacy_fallback(existing_marker: str) -> None:
    notes = [{"body": _build_body(_valid_payload(78), marker=existing_marker)}]
    assert parse_score_note(notes, marker="[CUSTOM_SCORE_V1]") is None


def test_returns_latest_valid_score_when_multiple_valid_notes_exist() -> None:
    # The orchestrator appends fresh scores after stale ones, so the parser
    # must honour the order of the input list: the last valid note wins.
    earlier = _build_body(_valid_payload(75, recommendation="APPLY", reasoning="good"))
    later = _build_body(_valid_payload(90, recommendation="REVIEW", reasoning="alt"))
    notes = [{"body": earlier}, {"body": later}]
    result = parse_score_note(notes)
    assert result is not None
    assert result["score"] == 90


def test_returns_latest_valid_score_among_three_mixed_notes() -> None:
    body_a = _build_body(_valid_payload(40, recommendation="SKIP", reasoning="a"))
    body_b = _build_body(_valid_payload(65, recommendation="APPLY", reasoning="b"))
    body_c = _build_body(_valid_payload(82, recommendation="PRIORITY_APPLY", reasoning="c"))
    notes = [{"body": body_a}, {"body": body_b}, {"body": body_c}]
    result = parse_score_note(notes)
    assert result is not None
    assert result["score"] == 82


def test_skips_invalid_notes_and_returns_later_valid_one() -> None:
    notes = [
        {"body": _build_body("not-json")},
        {"body": _build_body(_valid_payload(62, reasoning="ok"))},
    ]
    result = parse_score_note(notes)
    assert result is not None
    assert result["score"] == 62


def test_latest_invalid_score_falls_back_to_earlier_valid_one() -> None:
    earlier = _build_body(_valid_payload(55, reasoning="ok"))
    invalid_later = _build_body(_valid_payload(1000, reasoning="bad"))
    notes = [{"body": earlier}, {"body": invalid_later}]
    result = parse_score_note(notes)
    assert result is not None
    assert result["score"] == 55


def test_does_not_require_note_to_be_a_dict() -> None:
    notes = ["stray", 7, {"body": _build_body(_valid_payload(75, reasoning="good"))}]
    result = parse_score_note(notes)
    assert result is not None
    assert result["score"] == 75


def test_respects_custom_marker_argument() -> None:
    custom_marker = "[CUSTOM_SCORE_V1]"
    payload = '{"score":33,"recommendation":"SKIP"}'
    notes = [{"body": f"{custom_marker}\n{payload}"}]
    result = parse_score_note(notes, marker=custom_marker)
    assert result is not None
    assert result["score"] == 33
    # Default marker does NOT match.
    assert parse_score_note(notes) is None


def _eligible_payload(payload: dict[str, Any], threshold: int = 80) -> Any:
    helper = getattr(scoring, "eligible_score", None)
    assert callable(helper), "shared score eligibility policy is missing"
    return helper(payload, score_threshold=threshold)


def _additive_payload() -> dict[str, Any]:
    return {
        "score": 99, "fit_score": 80, "coverage_score": 80,
        "classification": "APPLY", "exclusion_signals": [],
        "hard_requirements_missing": [],
    }


@pytest.mark.parametrize("patch", [
    {"classification": "SKIP"}, {"recommendation": " skip "},
    {"hard_requirements_missing": ["License"]},
    {"exclusion_signals": ["Excluded employer"]},
    {"hard_requirements_missing": None}, {"exclusion_signals": True},
    {"exclusion_signals": "none"}, {"hard_requirements_missing": [1]},
    {"exclusion_signals": [" "]}, {"hard_requirements_missing": [""]},
    {"fit_score": True}, {"coverage_score": False},
    {"fit_score": 80.0}, {"coverage_score": "80"},
    {"fit_score": -1}, {"coverage_score": 101},
    {"classification": True}, {"classification": "UNKNOWN"},
    {"classification": None}, {"recommendation": True},
])
def test_eligibility_rejects_unsafe_payload(patch: dict[str, Any]) -> None:
    assert _eligible_payload({**_additive_payload(), **patch}) is None


@pytest.mark.parametrize("fields", [
    ("exclusion_signals",), ("hard_requirements_missing",),
    ("exclusion_signals", "hard_requirements_missing"),
    ("fit_score",), ("coverage_score",),
])
def test_eligibility_rejects_partial_additive_payload(fields: tuple[str, ...]) -> None:
    payload = _additive_payload()
    for field in fields:
        payload.pop(field)
    assert _eligible_payload(payload) is None


@pytest.mark.parametrize("marker", [
    "fit_score", "coverage_score", "classification", "evidence", "exclusion_signals",
])
def test_eligibility_rejects_lone_additive_marker(marker: str) -> None:
    assert _eligible_payload({"score": 99, marker: _additive_payload().get(marker, [])}) is None


@pytest.mark.parametrize("fit,coverage,classification", [
    (80, 80, "APPLY"), (80, 20, "REVIEW"), (20, 80, "EXPLORE"),
])
def test_eligibility_derives_class_without_mutating_note(
    fit: int, coverage: int, classification: str,
) -> None:
    payload = _additive_payload()
    payload.pop("classification")
    payload.update(fit_score=fit, coverage_score=coverage)
    normalized = _eligible_payload(payload)
    assert normalized == {**payload, "classification": classification}
    assert "classification" not in payload
    supplied = {**payload, "classification": "APPLY"}
    assert _eligible_payload(supplied) == {**supplied, "classification": classification}
    assert supplied["classification"] == "APPLY"


@pytest.mark.parametrize("payload", [
    {"score": 99, "fit_score": 20, "coverage_score": 20,
     "exclusion_signals": [], "hard_requirements_missing": []},
    {"score": 79}, {"score": True}, {"score": 101}, {"score": "99"},
    {"score": 99, "recommendation": "SKIP"},
    {"score": 99, "hard_requirements_missing": ["License"]},
])
def test_eligibility_keeps_threshold_and_legacy_vetoes(payload: dict[str, Any]) -> None:
    assert _eligible_payload(payload) is None


@pytest.mark.parametrize("payload", [
    {"score": 80}, {"score": 99, "recommendation": "PRIORITY_APPLY"},
    {"score": 91, "hard_requirements_missing": [], "strengths": ["Python"]},
])
def test_eligibility_preserves_legacy_payload(payload: dict[str, Any]) -> None:
    normalized = _eligible_payload(payload)
    assert normalized == payload
    assert normalized is not payload
