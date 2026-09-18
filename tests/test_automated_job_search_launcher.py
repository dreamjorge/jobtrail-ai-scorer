"""Tests for the discovery container resolution in the automated launcher.

``scripts/automated-job-search.example.py`` is not part of the installed
package (hyphenated filename), so it is loaded by path like the other
``scripts/*.py`` helpers tested elsewhere (see ``tests/test_runtime_backup.py``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobtrail_ai_scorer.n8n_outbound import DeliveryResult, N8nConfig

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "automated-job-search.example.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "automated_job_search_example", LAUNCHER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def launcher():
    return _load_module()


class _StopAfterCapture(Exception):
    """Raised once the discovery call args are captured, to short-circuit main()."""


def test_launcher_passes_configured_ats_boards_to_automation(
    monkeypatch, launcher, tmp_path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("JOBTRAIL_RUN_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))

    class FakeGateway:
        def __init__(self, base_url):
            self.base_url = base_url

        def close(self):
            pass

    class FakeAutomation:
        def __init__(self, gateway, *, seen_cache=None, ats_boards=None):
            captured["ats_boards"] = ats_boards

        def run(self, *, config):
            return SimpleNamespace(
                searched=0,
                imported=0,
                scored=0,
                selected=None,
                failures=(),
                profile_counts={},
            )

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", FakeGateway)
    monkeypatch.setattr(launcher, "JobSearchAutomation", FakeAutomation)
    monkeypatch.setattr(launcher, "_build_seen_cache", lambda args: None)
    monkeypatch.setenv(
        "JOB_ATS_BOARDS", '{"lever_boards":["acme"],"results_wanted":15}'
    )
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(
        sys,
        "argv",
        ["automated-job-search.example.py", "--no-discover"],
    )

    assert launcher.main() == 0
    assert captured["ats_boards"].lever_boards == ("acme",)
    assert captured["ats_boards"].results_wanted == 15


def test_final_output_includes_profile_counts(
    monkeypatch, capsys, launcher, tmp_path
) -> None:
    monkeypatch.setenv("JOBTRAIL_RUN_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))

    class FakeGateway:
        def __init__(self, base_url):
            self.base_url = base_url

        def close(self):
            pass

    class FakeAutomation:
        def __init__(self, gateway, *, seen_cache=None, ats_boards=None):
            self.gateway = gateway
            self.seen_cache = seen_cache

        def run(self, *, config):
            return SimpleNamespace(
                searched=1,
                imported=1,
                scored=1,
                selected={"title": "Python Engineer"},
                failures=(),
                profile_counts={
                    "python": {
                        "searched": 1,
                        "imported": 1,
                        "duplicates": 0,
                        "failures": 0,
                    }
                },
            )

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", FakeGateway)
    monkeypatch.setattr(launcher, "JobSearchAutomation", FakeAutomation)
    monkeypatch.setattr(launcher, "_build_seen_cache", lambda args: None)
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(
        sys,
        "argv",
        ["automated-job-search.example.py", "--no-discover"],
    )

    assert launcher.main() == 0
    output = capsys.readouterr().out
    assert "'profile_counts': {'python':" in output


def test_container_env_var_is_honored_when_flag_omitted(
    monkeypatch, launcher
) -> None:
    """JOBTRAIL_DISCOVER_CONTAINER must not be clobbered by argparse's default.

    Regression: --container previously defaulted to DISCOVER_DEFAULT_CONTAINER
    unconditionally, so omitting the flag always discarded an operator's
    JOBTRAIL_DISCOVER_CONTAINER in favor of "jobtrail-backend-1".
    """

    captured: dict[str, object] = {}

    def fake_resolve(config, *, container_name=None, **kwargs):
        captured["container_name"] = container_name
        captured["config_discover_container"] = config.discover_container
        raise _StopAfterCapture

    monkeypatch.setattr(launcher, "resolve_automation_base_url", fake_resolve)
    monkeypatch.setenv("JOBTRAIL_DISCOVER_CONTAINER", "my-custom-container")
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(sys, "argv", ["automated-job-search.example.py"])

    with pytest.raises(_StopAfterCapture):
        launcher.main()

    assert captured["container_name"] == "my-custom-container"
    assert captured["config_discover_container"] == "my-custom-container"


def test_container_flag_overrides_env_var(monkeypatch, launcher) -> None:
    """An explicit --container must still win over JOBTRAIL_DISCOVER_CONTAINER."""

    captured: dict[str, object] = {}

    def fake_resolve(config, *, container_name=None, **kwargs):
        captured["container_name"] = container_name
        raise _StopAfterCapture

    monkeypatch.setattr(launcher, "resolve_automation_base_url", fake_resolve)
    monkeypatch.setenv("JOBTRAIL_DISCOVER_CONTAINER", "env-container")
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(
        sys,
        "argv",
        ["automated-job-search.example.py", "--container", "cli-container"],
    )

    with pytest.raises(_StopAfterCapture):
        launcher.main()

    assert captured["container_name"] == "cli-container"


def _launcher_result(**overrides):
    values = {
        "run_id": "run-123",
        "searched": 2,
        "imported": 1,
        "scored": 1,
        "selected": None,
        "failures": (),
        "profile_counts": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_launcher_records_local_run_before_outbound_delivery(monkeypatch, launcher, tmp_path):
    events = []
    monkeypatch.setenv("JOBTRAIL_RUN_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(launcher, "record_run", lambda *args, **kwargs: events.append("run"))
    monkeypatch.setattr(launcher, "record_delivery", lambda *args, **kwargs: events.append("delivery"))

    class FakeAdapter:
        def __init__(self, config):
            pass

        def send(self, envelope):
            events.append("outbound")
            return DeliveryResult("accepted", 1, envelope["event_id"])

    monkeypatch.setattr(launcher, "N8nOutboundAdapter", FakeAdapter)
    launcher._record_local_then_deliver(
        _launcher_result(), SimpleNamespace(n8n=N8nConfig()), base_url_source="static"
    )

    assert events == ["run", "outbound", "delivery"]


def test_launcher_local_and_delivery_records_share_established_run_id(monkeypatch, launcher, tmp_path):
    journal = tmp_path / "journal.jsonl"
    monkeypatch.setenv("JOBTRAIL_RUN_JOURNAL_PATH", str(journal))
    launcher._record_local_then_deliver(
        _launcher_result(run_id="run-established"),
        SimpleNamespace(n8n=N8nConfig()),
        base_url_source="static",
        started_at=launcher.datetime.now(launcher.timezone.utc),
        finished_at=launcher.datetime.now(launcher.timezone.utc),
    )
    import json
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert records[0]["run_id"] == records[1]["run_id"] == "run-established"


def test_launcher_passes_distinct_pipeline_timestamps(monkeypatch, launcher, tmp_path):
    captured = {}
    monkeypatch.setenv("JOBTRAIL_RUN_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    moments = iter((launcher.datetime(2025, 1, 1, tzinfo=launcher.timezone.utc), launcher.datetime(2025, 1, 1, 0, 0, 1, tzinfo=launcher.timezone.utc)))
    monkeypatch.setattr(launcher, "datetime", lambda *args, **kwargs: next(moments))
    monkeypatch.setattr(launcher, "record_run", lambda path, result, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(launcher, "record_delivery", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "N8nOutboundAdapter", lambda config: SimpleNamespace(send=lambda envelope: DeliveryResult("disabled", 0, envelope["event_id"], "disabled")))
    launcher._record_local_then_deliver(
        _launcher_result(), SimpleNamespace(n8n=N8nConfig()), base_url_source="static",
        started_at=next(moments), finished_at=next(moments),
    )
    assert captured["started_at"] < captured["finished_at"]


def test_launcher_disabled_delivery_records_disabled_without_http(monkeypatch, launcher, tmp_path):
    events = []
    monkeypatch.setenv("JOBTRAIL_RUN_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(launcher, "record_run", lambda *args, **kwargs: events.append(("run", args[1])))
    monkeypatch.setattr(launcher, "record_delivery", lambda *args, **kwargs: events.append(("delivery", kwargs["result"])))

    def unexpected_http_client(*args, **kwargs):
        raise AssertionError("disabled delivery must not construct an HTTP client")

    monkeypatch.setattr("jobtrail_ai_scorer.n8n_outbound.httpx.Client", unexpected_http_client)
    launcher._record_local_then_deliver(
        _launcher_result(), SimpleNamespace(n8n=N8nConfig()), base_url_source="static"
    )

    assert events[0][0] == "run"
    assert events[1][0] == "delivery"
    assert events[1][1].status == "disabled"
    assert events[1][1].classification == "disabled"


def test_launcher_keeps_pipeline_failure_distinct_from_delivery_failure(monkeypatch, launcher, tmp_path):
    recorded = {}
    monkeypatch.setenv("JOBTRAIL_RUN_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(launcher, "record_run", lambda path, result, **kwargs: recorded.setdefault("run", result))
    monkeypatch.setattr(launcher, "record_delivery", lambda path, **kwargs: recorded.setdefault("delivery", kwargs["result"]))

    class FailedAdapter:
        def __init__(self, config):
            pass

        def send(self, envelope):
            return DeliveryResult("failed", 1, envelope["event_id"], "terminal")

    monkeypatch.setattr(launcher, "N8nOutboundAdapter", FailedAdapter)
    pipeline_result = _launcher_result(failures=("score:terminal:RuntimeError",))
    launcher._record_local_then_deliver(
        pipeline_result, SimpleNamespace(n8n=N8nConfig()), base_url_source="static"
    )

    assert recorded["run"].failures == ("score:terminal:RuntimeError",)
    assert recorded["delivery"].status == "failed"
    assert recorded["delivery"].classification == "terminal"


def test_container_defaults_when_neither_flag_nor_env_set(
    monkeypatch, launcher
) -> None:
    captured: dict[str, object] = {}

    def fake_resolve(config, *, container_name=None, **kwargs):
        captured["container_name"] = container_name
        raise _StopAfterCapture

    monkeypatch.setattr(launcher, "resolve_automation_base_url", fake_resolve)
    monkeypatch.delenv("JOBTRAIL_DISCOVER_CONTAINER", raising=False)
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(sys, "argv", ["automated-job-search.example.py"])

    with pytest.raises(_StopAfterCapture):
        launcher.main()

    assert captured["container_name"] == launcher.DISCOVER_DEFAULT_CONTAINER
