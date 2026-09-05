import json
from dataclasses import dataclass, field

import pytest

from jobtrail_ai_scorer.scoring import ScoreOutcome, score_jobs


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


def test_empty_description_is_skipped_without_fetching_full_record():
    client = FakeClient([candidate("j1", " \n ")], {"j1": full_job("j1")})

    result = score_jobs(client, FakeProvider(json.dumps(VALID_SCORE)), "Generic profile")

    assert result.processed == 0
    assert result.skipped == 1
    assert result.failed == 0
    assert client.get_calls == []
    assert client.notes == []


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
