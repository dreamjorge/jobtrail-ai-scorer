from dataclasses import dataclass, field

from jobtrail_ai_scorer.main import run_score
from jobtrail_ai_scorer.scoring import ScoreRunResult


@dataclass
class Client:
    def list_jobs(self):
        return []

    def close(self):
        pass


@dataclass
class Provider:
    prompts: list[str] = field(default_factory=list)

    def score(self, prompt: str):
        self.prompts.append(prompt)
        return "{}"


def test_run_score_includes_public_strategy_without_cv(tmp_path, monkeypatch):
    profile = tmp_path / "profile.md"
    profile.write_text("Public profile")
    strategy = tmp_path / "strategy.md"
    strategy.write_text(
        "## Target roles\nBackend\n\n"
        "## Required signals\nPython\n\n"
        "## Equivalent skills\nGo\n\n"
        "## Adjacent roles\nSRE\n\n"
        "## Exclusion rules\nNone\n\n"
        "## Location and seniority tolerance\nRemote\n\n"
        "## Evidence policy\nJob only\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        f"jobtrail_base_url: https://jobs.test\n"
        f"candidate_profile_path: {profile}\n"
        f"matching_strategy_path: {strategy}\n"
    )
    provider = Provider()
    captured: list[str] = []

    def fake_score_jobs(client, provider, candidate_profile, **kwargs):
        captured.append(candidate_profile)
        return ScoreRunResult(0, 0, 0, ())

    monkeypatch.setattr("jobtrail_ai_scorer.main.score_jobs", fake_score_jobs)
    run_score(
        config_path=config,
        client_factory=lambda _: Client(),
        provider_factory=lambda _: provider,
    )

    assert provider.prompts == []
    assert "Public profile" in captured[0]
    assert "Public matching strategy:" in captured[0]
    assert "Backend" in captured[0]
    assert "Candidate CV" not in captured[0]
