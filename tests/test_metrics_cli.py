import json
import time

from typer.testing import CliRunner

import jobtrail_ai_scorer.main as main


runner = CliRunner()


def test_metrics_json_reads_journal_cache_and_jobs(monkeypatch, tmp_path):
    journal = tmp_path / "runs.jsonl"
    now = time.time()
    journal.write_text(json.dumps({"started_at": now, "searched": 2, "imported": 1, "scored": 1, "scored_failed": 0, "notified": True, "finished_at": now, "failures": []}) + "\n")
    cache = tmp_path / "seen.json"
    cache.write_text(json.dumps({"version": 1, "entries": {"indeed\x1fabc": {"first_seen": now}}}))

    class FakeClient:
        def __init__(self, url):
            self.url = url
        def list_jobs(self):
            return [{"id": "safe", "score": 91, "scored_at": 1, "description": "secret"}]
        def close(self):
            pass

    monkeypatch.setattr(main, "JobTrailClient", FakeClient)
    result = runner.invoke(main.app, ["metrics", "--period", "7d", "--json", "--journal-path", str(journal), "--seen-cache-path", str(cache), "--base-url", "http://fake"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["period"] == "7d"
    assert payload["searched"] == 2
    assert "secret" not in result.stdout


def test_metrics_invalid_period_is_rejected():
    result = runner.invoke(main.app, ["metrics", "--period", "bad", "--json"])
    assert result.exit_code != 0
    assert result.exception is not None


def test_metrics_backend_failure_still_emits_missing_data(monkeypatch, tmp_path):
    class BrokenClient:
        def __init__(self, url):
            pass
        def list_jobs(self):
            raise RuntimeError("backend secret")
        def close(self):
            pass

    monkeypatch.setattr(main, "JobTrailClient", BrokenClient)
    result = runner.invoke(main.app, ["metrics", "--json", "--journal-path", str(tmp_path / "missing")])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["missing_data"]
    assert "backend secret" not in result.stdout
