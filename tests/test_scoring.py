import json
from dataclasses import dataclass, field

import pytest

from jobtrail_ai_scorer.models import ScoreResult
from jobtrail_ai_scorer.scoring import (
    ScoreOutcome,
    classify_score,
    parse_score_note,
    render_prompt,
    score_jobs,
    should_score,
)


VALID_SCORE = {
    "score": 85,
    "recommendation": "APPLY",
    "strengths": ["Relevant experience"],
    "gaps": [],
    "needs_confirmation": [],
    "hard_requirements_missing": [],
    "career_value": "High",
    "reasoning": "The role is a good fit.",
}


@dataclass
class FakeClient:
    jobs: list[dict]
    full_jobs: dict[str, dict]
    notes: list[tuple[str, str]] = field(default_factory=list)
    get_calls: list[str] = field(default_factory=list)

    def list_jobs(self) -> list[dict]:
        return self.jobs

    def get_job(self, job_id: str) -> dict:
        self.get_calls.append(job_id)
        value = self.full_jobs[job_id]
        if isinstance(value, Exception):
            raise value
        return value

    def add_note(self, job_id: str, body: str) -> None:
        self.notes.append((job_id, body))


@dataclass
class FakeProvider:
    response: str
    prompts: list[str] = field(default_factory=list)

    def score(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def candidate(job_id: str, description: str = "Job description") -> dict:
    return {"id": job_id, "description": description, "title": "Backend Engineer"}


def full_job(job_id: str, description: str = "Job description", notes=None) -> dict:
    return {
        "id": job_id,
        "description": description,
        "title": "Backend Engineer",
        "company": "Example Co",
        "notes": [] if notes is None else notes,
    }


def test_additive_prompt_contract_mentions_scores_evidence_and_classification():
    prompt = render_prompt(
        full_job("j1"),
        "Generic profile",
        strategy_context={"target_roles": ["Backend Engineer"]},
    )

    for literal in ("fit_score", "coverage_score", "evidence", "direct", "equivalent", "inferred", "missing", "classification"):
        assert literal in prompt
    assert "Backend Engineer" in prompt


def test_deterministic_classification_covers_all_classes_and_exclusions():
    assert classify_score(90, 80) == "APPLY"
    assert classify_score(70, 40) == "REVIEW"
    assert classify_score(60, 30) == "EXPLORE"
    assert classify_score(40, 90) == "SKIP"
    assert classify_score(90, 90, exclusion_signal=True) == "SKIP"
    assert classify_score(90, 90, critical_requirements_missing=["security clearance"]) == "SKIP"


@pytest.mark.parametrize(
    "fit_score,coverage_score",
    [(True, 80), (80, False), (80.5, 80), (80, "80"), (-1, 80), (80, 101)],
)
def test_direct_classification_rejects_invalid_score_types_and_ranges(fit_score, coverage_score):
    with pytest.raises(ValueError, match="scores must be strict integers between 0 and 100"):
        classify_score(fit_score, coverage_score)


def test_legacy_score_only_provider_response_gets_safe_defaults():
    result = ScoreResult.model_validate(VALID_SCORE)
    assert result.fit_score == 85
    assert result.coverage_score == 85
    assert result.classification == "APPLY"
    assert result.evidence == []


def test_missing_hard_requirement_normalizes_classification_to_skip():
    result = ScoreResult.model_validate(
        {
            **VALID_SCORE,
            "fit_score": 99,
            "coverage_score": 98,
            "classification": "APPLY",
            "hard_requirements_missing": ["security clearance"],
        }
    )

    assert result.classification == "SKIP"


def test_invalid_additive_provider_values_are_rejected():
    with pytest.raises(Exception):
        ScoreResult.model_validate({**VALID_SCORE, "fit_score": 101})
    with pytest.raises(Exception):
        ScoreResult.model_validate({**VALID_SCORE, "evidence": [{"label": "direct", "text": ""}]})
    with pytest.raises(Exception):
        ScoreResult.model_validate({**VALID_SCORE, "evidence": [{"label": "unsupported", "text": "claim"}]})


def test_evidence_accepts_each_explicit_label():
    for label in ("direct", "equivalent", "inferred", "missing"):
        result = ScoreResult.model_validate(
            {**VALID_SCORE, "evidence": [{"label": label, "text": "evidence"}]}
        )
        assert result.evidence[0].label == label


def test_note_round_trip_preserves_additive_fields_and_legacy_parser():
    payload = {**VALID_SCORE, "fit_score": 72, "coverage_score": 64, "classification": "REVIEW", "evidence": [{"label": "equivalent", "text": "Python maps to tooling"}]}
    client = FakeClient([candidate("j1")], {"j1": full_job("j1")})
    score_jobs(client, FakeProvider(json.dumps(payload)), "Generic profile")
    parsed = parse_score_note([{"body": client.notes[0][1]}])
    assert parsed is not None
    assert parsed["fit_score"] == 72
    assert parsed["evidence"][0]["label"] == "equivalent"


def test_render_prompt_enumerates_strict_score_result_contract():
    prompt = render_prompt(full_job("j1"), "Generic profile")

    expected_keys = (
        "score",
        "recommendation",
        "strengths",
        "gaps",
        "needs_confirmation",
        "hard_requirements_missing",
        "career_value",
        "reasoning",
    )
    for key in expected_keys:
        assert key in prompt

    for literal in ("PRIORITY_APPLY", "APPLY", "REVIEW", "SKIP"):
        assert literal in prompt

    for literal in ("High", "Medium", "Low"):
        assert literal in prompt

    prompt_lower = prompt.lower()
    assert "no extra fields" in prompt_lower
    assert "score must be an integer 0-100" in prompt_lower
    assert "strengths, gaps, needs_confirmation, and hard_requirements_missing" in prompt_lower
    assert "arrays of strings" in prompt_lower
    assert "reasoning must be a non-empty string" in prompt_lower


def test_empty_description_is_skipped_without_fetching_full_record():
    client = FakeClient([candidate("j1", " \n ")], {"j1": full_job("j1")})

    result = score_jobs(client, FakeProvider(json.dumps(VALID_SCORE)), "Generic profile")

    assert result.processed == 0
    assert result.skipped == 1
    assert result.failed == 0
    assert client.get_calls == []
    assert client.notes == []


@pytest.mark.parametrize("existing_marker", ["[AI_JOB_SCORE_V1]", "[HERMES_JOB_SCORE_V1]"])
def test_custom_marker_does_not_dedupe_current_or_legacy_notes(existing_marker):
    job = full_job("j1", notes=[{"body": f"{existing_marker}\n{json.dumps(VALID_SCORE)}"}])

    assert should_score(job, marker="[CUSTOM_SCORE_V1]") is True


def test_default_marker_dedupes_legacy_notes():
    job = full_job("j1", notes=[{"body": f"[HERMES_JOB_SCORE_V1]\n{json.dumps(VALID_SCORE)}"}])

    assert should_score(job) is False


def test_selected_candidate_fetches_full_record_before_inspecting_notes():
    client = FakeClient(
        [candidate("j1")],
        {"j1": full_job("j1", notes=[{"body": "[AI_JOB_SCORE_V1]"}])},
    )

    result = score_jobs(client, FakeProvider(json.dumps(VALID_SCORE)), "Generic profile")

    assert client.get_calls == ["j1"]
    assert result.skipped == 1
    assert result.outcomes[0].reason == "already_scored"


def test_invalid_provider_json_does_not_save_note():
    client = FakeClient([candidate("j1")], {"j1": full_job("j1")})

    result = score_jobs(client, FakeProvider("not json"), "Generic profile")

    assert result.processed == 0
    assert result.failed == 1
    assert client.notes == []
    assert result.outcomes[0].reason == "invalid_score"


@pytest.mark.parametrize(
    "response",
    [
        json.dumps(VALID_SCORE),
        "```json\n" + json.dumps(VALID_SCORE) + "\n```",
    ],
    ids=["plain-json", "fenced-json"],
)
def test_provider_accepts_plain_or_single_fenced_json(response):
    client = FakeClient([candidate("j1")], {"j1": full_job("j1")})

    result = score_jobs(client, FakeProvider(response), "Generic profile", dry_run=True)

    assert result.processed == 1
    assert result.failed == 0
    assert result.outcomes[0].status == "dry_run"


@pytest.mark.parametrize(
    "response",
    [
        "Here is the score: " + json.dumps(VALID_SCORE),
        json.dumps(VALID_SCORE) + json.dumps(VALID_SCORE),
    ],
    ids=["surrounding-prose", "repeated-objects"],
)
def test_provider_rejects_prose_or_repeated_json(response):
    client = FakeClient([candidate("j1")], {"j1": full_job("j1")})

    result = score_jobs(client, FakeProvider(response), "Generic profile")

    assert result.processed == 0
    assert result.failed == 1
    assert result.outcomes[0].reason == "invalid_score"
    assert client.notes == []


def test_dry_run_does_not_save_valid_note(capsys):
    client = FakeClient([candidate("j1")], {"j1": full_job("j1")})

    result = score_jobs(client, FakeProvider(json.dumps(VALID_SCORE)), "Generic profile", dry_run=True)

    assert result.processed == 1
    assert result.failed == 0
    assert client.notes == []
    assert result.outcomes[0].status == "dry_run"
    assert "DRY RUN j1" in capsys.readouterr().out


def test_valid_score_saves_marker_and_canonical_json_note():
    client = FakeClient([candidate("j1")], {"j1": full_job("j1")})

    provider = FakeProvider(json.dumps(VALID_SCORE))

    result = score_jobs(client, provider, "Generic profile")

    assert result.processed == 1
    assert result.skipped == 0
    assert result.failed == 0
    assert client.notes == [("j1", "[AI_JOB_SCORE_V1]\n" + json.dumps(VALID_SCORE, sort_keys=True, separators=(",", ":")))]
    assert "Generic profile" in provider.prompts[0]


def test_failure_for_one_job_does_not_prevent_independent_job():
    client = FakeClient(
        [candidate("broken"), candidate("good")],
        {"broken": RuntimeError("API unavailable"), "good": full_job("good")},
    )

    result = score_jobs(client, FakeProvider(json.dumps(VALID_SCORE)), "Generic profile")

    assert result.processed == 1
    assert result.failed == 1
    assert [outcome.job_id for outcome in result.outcomes] == ["broken", "good"]
    assert client.notes[0][0] == "good"


def test_job_id_selects_only_that_candidate_and_limit_restricts_selection():
    client = FakeClient(
        [candidate("j1"), candidate("j2")],
        {"j1": full_job("j1"), "j2": full_job("j2")},
    )
    provider = FakeProvider(json.dumps(VALID_SCORE))

    result = score_jobs(client, provider, "Generic profile", job_id="j2", limit=1)

    assert result.processed == 1
    assert client.get_calls == ["j2"]
    assert [job_id for job_id, _ in client.notes] == ["j2"]


def test_limit_in_list_mode_scores_only_first_candidate():
    client = FakeClient(
        [candidate("j1"), candidate("j2")],
        {"j1": full_job("j1"), "j2": full_job("j2")},
    )

    result = score_jobs(client, FakeProvider(json.dumps(VALID_SCORE)), "Generic profile", limit=1)

    assert result.processed == 1
    assert result.failed == 0
    assert client.get_calls == ["j1"]
    assert [job_id for job_id, _ in client.notes] == ["j1"]


def test_schema_invalid_provider_json_records_failure_without_saving_note():
    client = FakeClient([candidate("j1")], {"j1": full_job("j1")})
    invalid_score = {**VALID_SCORE, "score": "85"}

    result = score_jobs(client, FakeProvider(json.dumps(invalid_score)), "Generic profile")

    assert result.processed == 0
    assert result.failed == 1
    assert result.outcomes == (ScoreOutcome("j1", "failed", "invalid_score"),)
    assert client.notes == []


@pytest.mark.parametrize("marker", ["", " \t\n "])
def test_score_jobs_rejects_empty_or_whitespace_marker(marker):
    client = FakeClient([], {})

    with pytest.raises(ValueError, match="marker must not be empty or whitespace"):
        score_jobs(client, FakeProvider(json.dumps(VALID_SCORE)), "Generic profile", marker=marker)


def test_score_jobs_normalizes_marker_for_dedupe_and_note_serialization():
    marker = " [CUSTOM] "
    already_scored = FakeClient(
        [candidate("already")],
        {"already": full_job("already", notes=[{"body": "[CUSTOM]\n{}"}])},
    )

    skipped = score_jobs(
        already_scored, FakeProvider(json.dumps(VALID_SCORE)), "Generic profile", marker=marker
    )

    assert skipped.skipped == 1
    assert already_scored.notes == []

    new_job = FakeClient([candidate("new")], {"new": full_job("new")})
    score_jobs(new_job, FakeProvider(json.dumps(VALID_SCORE)), "Generic profile", marker=marker)

    assert new_job.notes[0][1].startswith("[CUSTOM]\n")
