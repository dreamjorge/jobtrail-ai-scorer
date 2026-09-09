import json
from dataclasses import dataclass, field

import pytest
from typer.testing import CliRunner

from jobtrail_ai_scorer import main
from jobtrail_ai_scorer.scoring import score_jobs


VALID = {
    "score": 85, "recommendation": "APPLY", "strengths": ["Python"],
    "gaps": ["Kubernetes"], "needs_confirmation": ["Location"],
    "hard_requirements_missing": [], "career_value": "High",
    "reasoning": "Strong fit.",
}


@dataclass
class Client:
    jobs: list[dict]
    full: dict[str, dict]
    notes: list = field(default_factory=list)

    def list_jobs(self): return self.jobs
    def get_job(self, job_id): return self.full[job_id]
    def add_note(self, job_id, body): self.notes.append((job_id, body))


@dataclass
class Provider:
    responses: list[str]

    def score(self, prompt): return self.responses.pop(0)


def job(job_id):
    return {"id": job_id, "description": "description", "title": "title",
            "url": "https://private.example", "metadata": {"secret": "x"}}


def test_dry_run_json_has_safe_validated_schema_and_no_note():
    client = Client([{"id": "j1", "description": "description"}], {"j1": job("j1")})
    result = score_jobs(client, Provider([json.dumps(VALID)]), "profile", dry_run=True,
                        output_json=True, emit_status=False)

    payload = result.json_output
    assert list(payload[0]) == sorted(payload[0])
    assert payload[0] == {"job_id": "j1", "status": "dry_run", **VALID, "reason": "validated"}
    assert client.notes == []


def test_multiple_outcomes_are_deterministic_json_and_bounded():
    client = Client([{"id": "b", "description": "description"},
                     {"id": "a", "description": "description"}],
                    {"a": job("a"), "b": job("b")})
    result = score_jobs(client, Provider([json.dumps(VALID), json.dumps(VALID)]), "profile",
                        dry_run=True, output_json=True, emit_status=False)
    assert result.json_text == json.dumps(result.json_output, sort_keys=True, separators=(",", ":"))
    assert [item["job_id"] for item in result.json_output] == ["b", "a"]
    assert len(result.json_text) < 2000


def test_malformed_outcome_is_safe_and_contains_no_sensitive_fields():
    client = Client([{"id": "j1", "description": "description"}], {"j1": job("j1")})
    result = score_jobs(client, Provider(["not json"]), "profile", dry_run=True,
                        output_json=True, emit_status=False)
    item = result.json_output[0]
    assert item == {"job_id": "j1", "status": "failed", "reason": "invalid_score"}
    assert not {"prompt", "profile", "description", "metadata", "url", "raw"} & set(item)


def test_json_is_rejected_without_dry_run():
    with pytest.raises(ValueError, match="dry_run"):
        score_jobs(Client([], {}), Provider([]), "profile", output_json=True)


def test_cli_exposes_json_flag(monkeypatch):
    captured = {}

    def fake_run_score(**kwargs):
        captured.update(kwargs)
        return type("Result", (), {"failed": 0})()

    monkeypatch.setattr(main, "run_score", fake_run_score)
    result = CliRunner().invoke(main.app, ["score", "--dry-run", "--json"])
    assert result.exit_code == 0
    assert captured["output_json"] is True
