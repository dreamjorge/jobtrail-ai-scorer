"""Focused tests for the public scorer command."""
import pytest
from pydantic import ValidationError

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


def test_run_score_warns_and_continues_when_profile_is_missing(caplog, tmp_path):
    profile_path = tmp_path / "missing-profile.md"
    cv_path = tmp_path / "cv.md"
    config_path = tmp_path / "cfg.yaml"
    cv_path.write_text("CV CONTENT")
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

    with caplog.at_level("WARNING"):
        result = main.run_score(
            config_path=config_path,
            job_id="j1",
            dry_run=True,
            client_factory=lambda _: FakeClient(),
            provider_factory=lambda _: FakeProvider(),
        )

    assert result.processed == 1
    assert "CV CONTENT" in prompts[0]
    assert "Unable to load candidate profile" in caplog.text


@pytest.mark.parametrize(
    ("override", "invalid_value", "field"),
    [
        ("provider_name", "not-a-provider", "provider"),
        ("base_url", "not-a-url", "jobtrail_base_url"),
    ],
)
def test_run_score_revalidates_overrides(tmp_path, override, invalid_value, field):
    config_path = _config(tmp_path)

    with pytest.raises(ValidationError, match=field):
        main.run_score(
            config_path=config_path,
            **{override: invalid_value},
            client_factory=lambda _: pytest.fail("invalid config reached client"),
            provider_factory=lambda _: pytest.fail("invalid config reached provider"),
        )


def test_run_score_uses_validated_overrides(tmp_path):
    config_path = _config(tmp_path)
    seen = {}

    class FakeClient:
        def close(self):
            pass

    def client_factory(url):
        seen["url"] = url
        return FakeClient()

    def provider_factory(config):
        seen["provider"] = config.provider
        return object()

    main.run_score(
        config_path=config_path,
        provider_name="openai_compatible",
        base_url="https://jobs.example.test",
        job_id="j1",
        dry_run=True,
        client_factory=client_factory,
        provider_factory=provider_factory,
    )

    assert seen == {
        "url": "https://jobs.example.test/",
        "provider": "openai_compatible",
    }


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


def test_feedback_cli_accepts_repeated_and_comma_labels(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    seen = {}
    monkeypatch.setattr(main, "run_feedback", lambda **kwargs: seen.update(kwargs) or {})
    result = runner.invoke(main.app, ["feedback", "--job-id", "j1", "--label", "other,good_match",
                                      "--label", "missing_skill", "--config", str(config_path)])
    assert result.exit_code == 0
    assert seen["labels"] == ["other", "good_match", "missing_skill"]


def test_run_feedback_fetches_before_write_closes_client_and_has_no_apply_side_effect(tmp_path):
    config_path = _config(tmp_path)
    calls = []

    class FakeClient:
        def get_job(self, job_id):
            calls.append(("get", job_id))
            return {"id": job_id, "source": "manual", "notes": [
                {"body": '[AI_JOB_SCORE_V1]\n{"score":80}'}
            ]}

        def add_note(self, job_id, body):
            calls.append(("note", job_id, body))

        def close(self):
            calls.append(("close",))

    result = main.run_feedback(config_path=config_path, job_id="j1",
                               labels=["good_match", "other"], client_factory=lambda _: FakeClient())
    assert result["labels"] == ("good_match", "other")
    assert [call[0] for call in calls] == ["get", "note", "close"]


@pytest.mark.parametrize("notes", [[], [{"body": '[AI_JOB_SCORE_V1]\\n{"score":101}'}]])
def test_run_feedback_rejects_missing_or_invalid_score_marker(notes, tmp_path):
    config_path = _config(tmp_path)
    state = {"writes": 0, "closed": False}

    class FakeClient:
        def get_job(self, job_id):
            return {"id": job_id, "notes": notes}

        def add_note(self, job_id, body):
            state["writes"] += 1

        def close(self):
            state["closed"] = True

    with pytest.raises(ValueError, match="no valid score marker"):
        main.run_feedback(config_path=config_path, job_id="j1", labels=["other"],
                          client_factory=lambda _: FakeClient())
    assert state == {"writes": 0, "closed": True}
