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

def test_intelligence_factory_disabled_and_dryrun_do_not_read_secret():
    module = _load_module()
    class Guard(dict):
        def get(self, key, default=None):
            assert key != 'BRAVE_SEARCH_API_KEY'
            return super().get(key, default)
    assert module._build_intelligence(Guard(), dry_run=False) == {}
    assert module._build_intelligence(Guard(OPPORTUNITY_INTELLIGENCE_ENABLED='true'), dry_run=True) == {}


def test_intelligence_factory_real_typed_missing_key_and_context_limits():
    module = _load_module()
    from jobtrail_ai_scorer.opportunity_intelligence import PublicJobIdentity, OpportunityIntelligence
    kwargs = module._build_intelligence({'OPPORTUNITY_INTELLIGENCE_ENABLED': 'true'})
    resolver = kwargs['intelligence_resolver_factory']()
    assert type(resolver) is OpportunityIntelligence
    job = PublicJobIdentity('Acme', 'Engineer', 'Remote', 'https://source.test/1', 'indeed', '1')
    assert resolver.resolve(job, enabled=True).status == 'provider_unconfigured'
    for raw in ['SECRET_SENTINEL', ' ' * 16385, '[{}]' , '[' + ','.join('{}' for _ in range(11)) + ']']:
        kwargs = module._build_intelligence({'OPPORTUNITY_INTELLIGENCE_ENABLED': 'true', 'OPPORTUNITY_EMPLOYER_CONTEXTS_JSON': raw})
        assert kwargs['intelligence_status'] == 'invalid_configuration'
        assert 'SECRET_SENTINEL' not in repr(kwargs)


@pytest.mark.parametrize('enabled', [False, True])
def test_canonical_launcher_real_pipeline_mock_public_io(monkeypatch, enabled):
    import json
    import httpx
    from datetime import datetime, timezone
    from jobtrail_ai_scorer.automation import JobSearchAutomation
    from jobtrail_ai_scorer import public_search, public_http
    from jobtrail_ai_scorer.opportunity_intelligence import PublicJobIdentity
    module = _load_module()
    env = {'SCORER_CONFIG_PATH': 'synthetic', 'JOB_SEARCH_LOCATIONS': 'Austin',
           'WHATSAPP_NOTIFY_ENABLED': 'true', 'N8N_ENABLED': 'true',
           'N8N_ENDPOINT': 'https://n8n.test/hook'}
    if enabled:
        env.update(OPPORTUNITY_INTELLIGENCE_ENABLED='true', BRAVE_SEARCH_API_KEY='synthetic-test-key',
                   OPPORTUNITY_EMPLOYER_CONTEXTS_JSON=json.dumps([{
                       'company': 'Acme', 'hosts': ['acme.example'],
                       'careers_url': 'https://acme.example/careers',
                       'provenance': {'url': 'https://acme.example/careers',
                                      'excerpt': 'Operator confirmed public employer page',
                                      'checked_at': '2026-06-01T00:00:00+00:00', 'kind': 'user_confirmed'}}]))
    # Replace, rather than inspecting/copying, the ambient environment.
    monkeypatch.setattr(module.os, 'environ', env)
    monkeypatch.setattr(sys, 'argv', ['launcher', '--no-discover'])
    messages, runs, constructed, requests, fetches = [], [], [], [], []
    class Gateway:
        def __init__(self, *args):
            self.jobs = {}
        def close(self):
            pass
        def search(self, payload):
            return [{'site': 'indeed', 'id': str(i), 'title': 'Engineer', 'company': 'Acme',
                     'location': 'Austin', 'job_url': f'https://source.example/{i}'} for i in range(4)]
        def import_job(self, payload):
            key = payload['sourceJobId']
            self.jobs[key] = dict(payload, id=key, notes=[], profile={'cv': 'RESUME_SENTINEL'},
                                  private_key='CREDENTIAL_SENTINEL', reasoning='PROMPT_SENTINEL')
            return {'id': key}
        def get_job(self, key):
            return self.jobs[key]
    class Automation(JobSearchAutomation):
        def __init__(self, gateway, **kwargs):
            if enabled:
                assert callable(kwargs['intelligence_resolver_factory'])
            def score(key, path):
                gateway.jobs[key]['notes'] = [{'body': '[AI_JOB_SCORE_V1]\n' + json.dumps({
                    'score': 91, 'recommendation': 'APPLY', 'strengths': [], 'gaps': []})}]
            super().__init__(gateway, scorer=score, notifier=messages.append, **kwargs)
        def run(self, **kwargs):
            result = super().run(**kwargs)
            runs.append(result)
            return result
    real_search = public_search.BravePublicSearchAdapter
    def respond(request):
        requests.append(request)
        assert 'RESUME_SENTINEL' not in str(request.url)
        return httpx.Response(200, json={'web': {'results': [{'title': 'Engineer',
            'url': f'https://jobs.lever.co/acme/{len(requests)}', 'description': 'untrusted lead'}]}})
    def search_factory(config):
        constructed.append('search')
        return real_search(config, transport=httpx.MockTransport(respond))
    class Fetch:
        def __init__(self):
            constructed.append('fetch')
        def fetch(self, url):
            fetches.append(url)
            now = datetime.now(timezone.utc).isoformat()
            if url == 'https://acme.example/careers':
                text = '<a href="https://jobs.lever.co/acme">Careers</a>'
            else:
                text = '<script type="application/ld+json">' + json.dumps({
                    '@type': 'JobPosting', 'title': 'Engineer',
                    'hiringOrganization': {'name': 'Acme'},
                    'jobLocation': {'address': {'addressLocality': 'Austin'}},
                    'sameAs': 'https://source.example/0', 'validThrough': '2099-01-01'}) + f'</script><a href="/acme/{len(requests)}/apply">Apply now</a>'
            return public_http.FetchResult('ok', url, now, 'text/html', text)
    monkeypatch.setattr(public_search, 'BravePublicSearchAdapter', search_factory)
    monkeypatch.setattr(public_http, 'PublicFetcher', Fetch)
    monkeypatch.setattr(module, 'JobTrailHTTPClient', Gateway)
    monkeypatch.setattr(module, 'JobSearchAutomation', Automation)
    monkeypatch.setattr(module, '_build_seen_cache', lambda args: None)
    delivered = []
    from jobtrail_ai_scorer.n8n_outbound import N8nOutboundAdapter
    monkeypatch.setattr(module, 'record_run', lambda *a, **k: None)
    monkeypatch.setattr(module, 'record_delivery', lambda *a, **k: None)
    monkeypatch.setattr(module, 'N8nOutboundAdapter', lambda config: N8nOutboundAdapter(
        config,
        transport=httpx.MockTransport(lambda request: (delivered.append(json.loads(request.content)) or httpx.Response(202)))))
    assert module.main() == 0
    assert len(delivered) == 1
    assert delivered[0]['schema_version'] == (2 if enabled else 1)
    if enabled:
        assert len(delivered[0]['opportunities']) == 3
        assert delivered[0]['opportunities'][0]['status'] == 'verified'
        assert delivered[0]['opportunities'][0]['original_url'] == 'https://source.example/0'
    else:
        assert 'opportunities' not in delivered[0]
    assert len(runs[0].opportunities) == 3
    assert runs[0].selected == runs[0].opportunities[0]
    assert runs[0].scored == 4 and len(messages) == 1
    if enabled:
        assert constructed == ['search', 'fetch']
        assert len(requests) == 3 and len(fetches) <= 6
        assert runs[0].intelligence[0].status == 'verified'
        assert 'Official (verified)' in messages[0]
        assert 'Original: https://source.example/0' in messages[0]
        assert 'Candidate (unverified)' in messages[0]
        assert not any(s in messages[0] for s in ('RESUME_SENTINEL', 'PROMPT_SENTINEL', 'CREDENTIAL_SENTINEL'))
    else:
        assert constructed == requests == fetches == []
        assert runs[0].intelligence == ()
        assert 'Public information' not in messages[0]


@pytest.mark.parametrize('enabled', [False, True])
def test_enrichment_budget_starts_after_slow_scoring_and_is_shared(monkeypatch, enabled):
    import json
    from jobtrail_ai_scorer.automation import AutomationConfig, JobSearchAutomation
    from jobtrail_ai_scorer import public_http, public_search, opportunity_intelligence
    module = _load_module()
    monkeypatch.setattr(module.os, 'environ', {})
    clock = [0.0]
    created, deadlines, dns_calls, statuses = [], [], [], []
    real_fetcher = public_http.PublicFetcher

    def dns(host, timeout):
        dns_calls.append(clock[0])
        clock[0] += 50
        raise TimeoutError()

    def fetch_factory():
        created.append(clock[0])
        return real_fetcher(clock=lambda: clock[0], resolver=dns)

    class Resolver:
        def __init__(self, search, fetch):
            self.fetch = fetch
        def resolve(self, job, context, **kwargs):
            deadlines.append(self.fetch._deadline)
            statuses.append(self.fetch.fetch(job.original_url).status)
            return opportunity_intelligence.OpportunityResult('unavailable', job.original_url)

    class Gateway:
        def __init__(self):
            self.jobs = {}
        def search(self, payload):
            return [{'site': 'indeed', 'id': str(i), 'title': 'Engineer', 'company': 'Acme',
                     'location': 'Remote', 'job_url': f'https://source.example/{i}'} for i in range(3)]
        def import_job(self, payload):
            key = payload['sourceJobId']
            self.jobs[key] = dict(payload, id=key)
            return {'id': key}
        def get_job(self, key):
            return self.jobs[key]

    gateway = Gateway()
    def score(key, path):
        clock[0] += 40
        gateway.jobs[key]['notes'] = [{'body': '[AI_JOB_SCORE_V1]\n' + json.dumps({
            'score': 91, 'recommendation': 'APPLY', 'strengths': [], 'gaps': []})}]

    monkeypatch.setattr(public_http, 'PublicFetcher', fetch_factory)
    monkeypatch.setattr(public_search, 'BravePublicSearchAdapter', lambda config: object())
    monkeypatch.setattr(opportunity_intelligence, 'OpportunityIntelligence', Resolver)
    class Guard(dict):
        def get(self, key, default=None):
            if key == 'BRAVE_SEARCH_API_KEY':
                assert enabled and clock[0] >= 120
            return super().get(key, default)
    kwargs = module._build_intelligence(Guard(OPPORTUNITY_INTELLIGENCE_ENABLED='true'))
    runner = JobSearchAutomation(gateway, scorer=score, **kwargs)
    config = AutomationConfig(scorer_config_path='synthetic', locations=('Remote',),
                              opportunity_intelligence_enabled=enabled)
    result = runner.run(config=config)
    assert result.scored == 3
    if not enabled:
        assert result.intelligence == ()
        assert created == deadlines == dns_calls == statuses == []
        return
    assert len(result.intelligence) == 3
    assert created == [120.0]
    assert deadlines == [210.0] * 3
    assert dns_calls == [120.0, 170.0]
    assert statuses == ['timed_out'] * 3
    # Reusing the automation object must not reuse the previous run's stack.
    runner.run(config=config)
    assert created == [120.0, 340.0]
    assert deadlines == [210.0] * 3 + [430.0] * 3
    assert dns_calls == [120.0, 170.0, 340.0, 390.0]


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


@pytest.mark.parametrize("enabled", [False, True])
def test_launcher_feedback_actions_are_explicit_opt_in(monkeypatch, enabled):
    module = _load_module()
    from jobtrail_ai_scorer.automation import AutomationConfig, AutomationRun

    monkeypatch.setattr(module, "record_run", lambda *a, **k: None)
    monkeypatch.setattr(module, "record_delivery", lambda *a, **k: None)
    captured = []

    class Adapter:
        def __init__(self, config):
            pass

        def send(self, envelope):
            captured.append(envelope)
            return DeliveryResult("disabled")

    monkeypatch.setattr(module, "N8nOutboundAdapter", Adapter)
    env = {"N8N_FEEDBACK_ACTIONS_ENABLED": "true"} if enabled else {}
    config = AutomationConfig.from_env(env)
    run = AutomationRun(run_id="synthetic-run", selected={
        "source": "synthetic", "sourceJobId": "job-1", "score": 90,
    })
    module._record_local_then_deliver(run, config, base_url_source="static")
    assert ("actions" in captured[0]) is enabled
    if enabled:
        assert [action["action"] for action in captured[0]["actions"]] == [
            "applied", "dismissed", "interesting",
        ]


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
