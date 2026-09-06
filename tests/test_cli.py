"""Focused tests for the public scorer command."""
import pytest

typer = pytest.importorskip("typer")  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from jobtrail_ai_scorer import main  # noqa: E402
from jobtrail_ai_scorer.scoring import ScoreOutcome, ScoreRunResult  # noqa: E402


runner = CliRunner()


def _config(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("profile")
    config = tmp_path / "cfg.yaml"
    config.write_text(
        f"jobtrail_base_url: http://localhost:3000\ncandidate_profile_path: {profile}\n"
        "provider: hermes\nhermes_executable: hermes\nhermes_profile: default\n"
    )
    return config


def _result(*, failed=0):
    return ScoreRunResult(1, 0, failed, (ScoreOutcome("j1", "saved" if not failed else "failed", "saved"),))


def test_run_score_passes_profile_and_cv_to_provider_prompt(tmp_path):
    profile_path = tmp_path / "profile.md"
    cv_path = tmp_path / "cv.md"
    config_path = tmp_path / "cfg.yaml"
    profile_path.write_text("PROFILE CONTENT")
    cv_path.write_text("PRIVATE CV CONTENT")
    config_path.write_text(
        f"jobtrail_base_url: http://localhost:3000\n"
        f"candidate_profile_path: {profile_path}\n"
        f"candidate_cv_path: {cv_path}\n"
    )
    prompts = []

    class FakeClient:
        def get_job(self, job_id):
            return {"id": job_id, "description": "A job"}

        def close(self):
            pass

    class FakeProvider:
        def score(self, prompt):
            prompts.append(prompt)
            return ('{"score": 80, "recommendation": "APPLY", '
                    '"strengths": [], "gaps": [], "needs_confirmation": [], '
                    '"hard_requirements_missing": [], "career_value": "High", '
                    '"reasoning": "matches"}')

    result = main.run_score(
        config_path=config_path,
        job_id="j1",
        dry_run=True,
        client_factory=lambda _: FakeClient(),
        provider_factory=lambda _: FakeProvider(),
    )

    assert result.processed == 1
    assert "PROFILE CONTENT" in prompts[0]
    assert "PRIVATE CV CONTENT" in prompts[0]


def test_score_forwards_flags_and_emits_once(monkeypatch, tmp_path):
    calls = {}
    config_path = _config(tmp_path)

    def fake_run_score(**kwargs):
        calls.update(kwargs)
        return _result()

    monkeypatch.setattr(main, "run_score", fake_run_score)
    result = runner.invoke(main.app, ["score", "--job-id", "j1", "--limit", "2", "--force",
                                      "--dry-run", "--marker", "[X]", "--provider", "hermes",
                                      "--config", str(config_path)])
    assert result.exit_code == 0
    assert calls["job_id"] == "j1"
    assert calls["limit"] == 2
    assert calls["force"] is True and calls["dry_run"] is True
    assert calls["marker"] == "[X]" and calls["provider_name"] == "hermes"
    assert calls["config_path"] == config_path


def test_score_returns_nonzero_on_failures(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    monkeypatch.setattr(main, "run_score", lambda **_: _result(failed=1))
    result = runner.invoke(main.app, ["score", "--config", str(config_path)])
    assert result.exit_code == 1


def test_cli_does_not_duplicate_status_lines(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    monkeypatch.setattr(main, "run_score", lambda **_: ScoreRunResult(
        1, 1, 0, (ScoreOutcome("j1", "skipped", "already_scored"),)))
    result = runner.invoke(main.app, ["score", "--config", str(config_path)])
    assert result.output.count("SKIP j1") == 0  # run_score is injectable and owns output here
