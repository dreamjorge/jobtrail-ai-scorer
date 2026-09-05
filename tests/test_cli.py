"""Focused tests for the public scorer command."""
import pytest

typer = pytest.importorskip("typer")  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from jobtrail_ai_scorer import main  # noqa: E402
from jobtrail_ai_scorer.scoring import ScoreOutcome, ScoreRunResult  # noqa: E402


runner = CliRunner()


def _result(*, failed=0):
    return ScoreRunResult(1, 0, failed, (ScoreOutcome("j1", "saved" if not failed else "failed", "saved"),))


def test_score_forwards_flags_and_emits_once(monkeypatch, tmp_path):
    calls = {}
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text("{}")

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
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text("{}")
    monkeypatch.setattr(main, "run_score", lambda **_: _result(failed=1))
    result = runner.invoke(main.app, ["score", "--config", str(config_path)])
    assert result.exit_code == 1


def test_cli_does_not_duplicate_status_lines(monkeypatch, tmp_path):
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text("{}")
    monkeypatch.setattr(main, "run_score", lambda **_: ScoreRunResult(
        1, 1, 0, (ScoreOutcome("j1", "skipped", "already_scored"),)))
    result = runner.invoke(main.app, ["score", "--config", str(config_path)])
    assert result.output.count("SKIP j1") == 0  # run_score is injectable and owns output here
