"""Focused tests for the public scorer command."""
import json

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


def test_track_is_read_only_and_json_is_bounded(monkeypatch, tmp_path):
    calls = []

    class FakeClient:
        def __init__(self, _url):
            pass
        def get_job(self, job_id):
            return {"id": job_id, "description": "private", "source_url": "https://secret", "notes": []}
        def add_note(self, *_args):
            calls.append("add_note")
        def close(self):
            pass

    monkeypatch.setattr(main, "JobTrailClient", FakeClient)
    config_path = _config(tmp_path)
    result = runner.invoke(main.app, ["track", "--job-id", "j1", "--json", "--config", str(config_path)])
    assert result.exit_code == 0
    assert '"current_state":"new"' in result.output
    assert "description" not in result.output and "https://secret" not in result.output
    assert calls == []


def test_track_fetches_before_transition_and_applied_needs_confirmation(monkeypatch, tmp_path):
    calls = []
    class FakeClient:
        def __init__(self, _url): pass
        def get_job(self, job_id): calls.append("get"); return {"id": job_id, "notes": []}
        def add_note(self, *_args): calls.append("add")
        def close(self): pass
    monkeypatch.setattr(main, "JobTrailClient", FakeClient)
    config_path = _config(tmp_path)
    args = ["--config", str(config_path)]
    result = runner.invoke(main.app, ["track", "--job-id", "j1", "--to", "scored", *args])
    assert result.exit_code == 0 and calls == ["get", "add"]
    result = runner.invoke(main.app, ["track", "--job-id", "j1", "--to", "applied", *args])
    assert result.exit_code != 0 and calls == ["get", "add", "get"]


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


def test_run_feedback_accepts_configured_score_marker_and_writes_one_feedback_note(tmp_path):
    config_path = _config(tmp_path)
    config_path.write_text(config_path.read_text() + 'marker: "[CUSTOM_SCORE]"\n')
    writes = []

    class FakeClient:
        def get_job(self, job_id):
            return {"id": job_id, "notes": [
                {"body": '[CUSTOM_SCORE]\n{"score":80}'}
            ]}

        def add_note(self, job_id, body):
            writes.append((job_id, body))

        def close(self):
            pass

    result = main.run_feedback(config_path=config_path, job_id="j1", labels=["good_match"],
                               client_factory=lambda _: FakeClient())

    assert result["labels"] == ("good_match",)
    assert len(writes) == 1
    assert writes[0][0] == "j1"
    assert writes[0][1].startswith("[AI_JOB_FEEDBACK_V1]")


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


def test_track_uses_config_url_and_source_job_id(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    writes = []

    class FakeClient:
        def __init__(self, url):
            assert url == "http://localhost:3000/"
        def get_job(self, job_id):
            return {"id": job_id, "source": "board", "sourceJobId": "remote-1", "position": "Engineer", "notes": []}
        def add_note(self, job_id, body):
            writes.append(body)
        def close(self):
            pass

    monkeypatch.setattr(main, "JobTrailClient", FakeClient)
    result = runner.invoke(main.app, ["track", "--job-id", "internal-1", "--to", "scored", "--config", str(config_path)])
    assert result.exit_code == 0
    assert '"source_job_id":"remote-1"' in writes[0]
    assert '"title":"Engineer"' in writes[0]


def test_track_source_override_matches_event_provenance(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    writes = []

    class FakeClient:
        def __init__(self, _url):
            pass
        def get_job(self, job_id):
            return {"id": job_id, "source": "board", "sourceJobId": "remote-1", "notes": []}
        def add_note(self, _job_id, body):
            writes.append(body)
        def close(self):
            pass

    monkeypatch.setattr(main, "JobTrailClient", FakeClient)
    result = runner.invoke(main.app, [
        "track", "--job-id", "internal-1", "--to", "scored", "--source", "manual",
        "--config", str(config_path),
    ])
    assert result.exit_code == 0
    assert '"source":"manual"' in writes[0]


def test_import_jobright_preview_requires_confirmation_and_redacts_description(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    calls = []
    class FakeClient:
        def __init__(self, _url): pass
        def list_jobs(self): calls.append("list"); return []
        def import_job(self, payload): calls.append(("import", payload)); return {"id": "j1"}
        def close(self): calls.append("close")
    monkeypatch.setattr(main, "JobTrailHTTPClient", FakeClient)
    result = runner.invoke(main.app, ["import-jobright", "--url", "https://jobright.example/jobs/1", "--title", "Engineer", "--company", "Acme", "--location", "Remote", "--description", "PRIVATE DESCRIPTION", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "Engineer" in result.output and "Acme" in result.output
    assert "PRIVATE DESCRIPTION" not in result.output
    assert calls == []


def test_import_jobright_confirm_imports_once_and_marks_cache_after_success(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    calls = []
    scorer_payloads = []
    class Cache:
        def mark_seen(self, source, source_job_id): calls.append(("cache", source, source_job_id))
    class FakeClient:
        def __init__(self, _url): pass
        def list_jobs(self): calls.append("list"); return []
        def import_job(self, payload): calls.append(("import", payload)); return {"id": "j1"}
        def close(self): calls.append("close")
    class FakeAutomation:
        def __init__(self, _client): pass
        def score_imported_job(self, _job_id, payload, *, config):
            scorer_payloads.append(payload)
            return {"scored": False, "notification_sent": False}
    monkeypatch.setattr(main, "JobSearchAutomation", FakeAutomation)
    monkeypatch.setattr(main, "JobTrailHTTPClient", FakeClient)
    monkeypatch.setattr(main, "SeenCache", lambda _path: Cache())
    result = runner.invoke(main.app, ["import-jobright", "--url", "https://jobright.example/jobs/1", "--title", "Engineer", "--company", "Acme", "--location", "Remote", "--description", "PRIVATE DESCRIPTION", "--confirm", "--config", str(config_path)])
    assert result.exit_code == 0
    assert [item if isinstance(item, str) else item[0] for item in calls] == ["list", "import", "cache", "close"]
    assert calls[1][1].get("description") is None
    assert scorer_payloads == [{"title": "Engineer", "company": "Acme", "location": "Remote", "source": "jobright_manual", "sourceUrl": "https://jobright.example/jobs/1", "description": "PRIVATE DESCRIPTION"}]
    assert "PRIVATE DESCRIPTION" not in result.output


def test_import_jobright_dry_run_overrides_confirm(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    calls = []
    class FakeClient:
        def __init__(self, _url): calls.append("client")
        def import_job(self, _payload): calls.append("import"); return {"id": "j1"}
        def close(self): calls.append("close")
    monkeypatch.setattr(main, "JobTrailHTTPClient", FakeClient)
    result = runner.invoke(main.app, ["import-jobright", "--url", "https://jobright.example/jobs/1", "--title", "Engineer", "--company", "Acme", "--location", "Remote", "--confirm", "--dry-run", "--config", str(config_path)])
    assert result.exit_code == 0 and "dry-run" in result.output
    assert calls == []


def test_import_jobright_duplicate_does_not_post(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    calls = []
    job = {"url": "https://jobright.example/jobs/1", "title": "Engineer", "company": "Acme", "location": "Remote"}
    source_job_id = main.normalize_jobright_manual(job).source_job_id
    existing = {"id": "existing", "source": "jobright_manual", "sourceJobId": source_job_id,
                "title": "Engineer", "company": "Acme", "location": "Remote",
                "sourceUrl": job["url"], "description": "PRIVATE EXISTING DESCRIPTION",
                "notes": [{"body": "PRIVATE NOTE"}]}
    class FakeClient:
        def __init__(self, _url): pass
        def list_jobs(self): return [existing]
        def import_job(self, _payload): calls.append("import"); return {"id": "new"}
        def close(self): calls.append("close")
    class NeverAutomation:
        def __init__(self, _client): calls.append("scoring")
    monkeypatch.setattr(main, "JobTrailHTTPClient", FakeClient)
    monkeypatch.setattr(main, "JobSearchAutomation", NeverAutomation)
    result = main.run_import_jobright(config_path=config_path, url=job["url"], title=job["title"],
                                      company=job["company"], location=job["location"], confirm=True)
    assert result["duplicate"] is True
    assert result["existing"] == {"id": "existing", "source": "jobright_manual", "sourceJobId": source_job_id,
                                   "title": "Engineer", "company": "Acme", "location": "Remote",
                                   "url": job["url"]}
    assert calls == ["close"]
    assert "PRIVATE EXISTING DESCRIPTION" not in json.dumps(result)


def test_import_jobright_url_only_prompts_for_required_fields(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    calls = []
    class FakeClient:
        def __init__(self, _url): calls.append("client")
        def close(self): pass
    monkeypatch.setattr(main, "JobTrailHTTPClient", FakeClient)
    result = runner.invoke(main.app, ["import-jobright", "--url", "https://jobright.example/jobs/1", "--config", str(config_path)], input="Engineer\nAcme\nRemote\n\n")
    assert result.exit_code == 0
    assert "Engineer" in result.output and calls == []


def test_import_jobright_backend_failure_does_not_mark_cache(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    marked = []
    class Cache:
        def mark_seen(self, *_args): marked.append(True)
    class FakeClient:
        def __init__(self, _url): pass
        def list_jobs(self): return []
        def import_job(self, _payload): raise RuntimeError("backend unavailable")
        def close(self): pass
    monkeypatch.setattr(main, "JobTrailHTTPClient", FakeClient)
    monkeypatch.setattr(main, "SeenCache", lambda _path: Cache())
    result = runner.invoke(main.app, ["import-jobright", "--url", "https://jobright.example/jobs/1", "--title", "Engineer", "--company", "Acme", "--location", "Remote", "--confirm", "--config", str(config_path)])
    assert result.exit_code != 0 and marked == []


def test_import_jobright_invalid_input_has_no_client(monkeypatch, tmp_path):
    config_path = _config(tmp_path)
    monkeypatch.setattr(main, "JobTrailHTTPClient", lambda _: pytest.fail("client must not be created"))
    result = runner.invoke(main.app, ["import-jobright", "--url", "http://not-https", "--confirm", "--config", str(config_path)])
    assert result.exit_code != 0
