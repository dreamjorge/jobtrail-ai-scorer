from datetime import datetime, timezone

import httpx

from jobtrail_ai_scorer.n8n_outbound import (
    DeliveryResult,
    N8nConfig,
    N8nOutboundAdapter,
    build_envelope,
)


def _envelope():
    return build_envelope(
        run_id="run-1",
        occurred_at="2025-01-01T00:00:00+00:00",
        searched=4,
        imported=2,
        scored=2,
        failures=("score:terminal:RuntimeError",),
        selected={
            "title": "T" * 300,
            "company": "C",
            "location": "Remote",
            "score": 91,
            "recommendation": "APPLY",
            "recommendationLabel": "Apply",
            "jobUrl": "https://jobs.test/1",
            "jobTrailLink": "https://jobtrail.test/jobs/1",
            "source": "indeed",
            "sourceJobId": "1",
            "description": "PRIVATE DESCRIPTION",
            "notes": "PRIVATE NOTES",
        },
    )


def test_envelope_is_versioned_bounded_allowlisted_and_stable():
    first = _envelope()
    second = _envelope()
    assert first == second
    assert set(first) == {"schema_version", "event_id", "run_id", "event_type", "occurred_at", "result", "selected"}
    assert first["schema_version"] == 1
    assert len(first["event_id"]) == 32
    assert first["result"] == {
        "searched": 4,
        "imported": 2,
        "scored": 2,
        "failure_count": 1,
        "failures": ["score:terminal:RuntimeError"],
    }
    assert len(first["selected"]["title"]) == 200
    assert "description" not in str(first)
    assert "PRIVATE" not in str(first)


def test_adapter_acknowledges_2xx_and_reuses_event_id_on_retry():
    requests = []
    transport = httpx.MockTransport(lambda request: (requests.append(request) or httpx.Response(202)))
    adapter = N8nOutboundAdapter(N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport)
    envelope = _envelope()
    result = adapter.send(envelope)
    assert result == DeliveryResult("accepted", 1, envelope["event_id"])
    assert requests[0].json()["event_id"] == envelope["event_id"]


def test_adapter_does_not_retry_4xx_but_retries_5xx_and_timeout():
    attempts = []
    def handler(request):
        attempts.append(request)
        return httpx.Response(500 if len(attempts) < 3 else 204)
    adapter = N8nOutboundAdapter(
        N8nConfig(enabled=True, endpoint="https://n8n.test/hook", retry_attempts=3),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    assert adapter.send(_envelope()).status == "accepted"
    assert len(attempts) == 3

    terminal_attempts = []
    adapter = N8nOutboundAdapter(
        N8nConfig(enabled=True, endpoint="https://n8n.test/hook"),
        transport=httpx.MockTransport(lambda request: (terminal_attempts.append(request) or httpx.Response(400))),
    )
    assert adapter.send(_envelope()).status == "failed"
    assert adapter.send(_envelope()).classification == "terminal"
    assert len(terminal_attempts) == 2


def test_disabled_missing_endpoint_and_dry_run_never_send():
    calls = []
    transport = httpx.MockTransport(lambda request: (calls.append(request) or httpx.Response(204)))
    envelope = _envelope()
    for config in (N8nConfig(), N8nConfig(enabled=True, endpoint="")):
        result = N8nOutboundAdapter(config, transport=transport).send(envelope)
        assert result.status == "disabled"
    assert N8nOutboundAdapter(N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport).send(envelope, dry_run=True).status == "disabled"
    assert calls == []


def test_config_is_disabled_by_default_and_parses_bounded_values():
    config = N8nConfig.from_env({})
    assert config.enabled is False
    assert config.endpoint == ""
    assert N8nConfig.from_env({"N8N_ENABLED": "1", "N8N_ENDPOINT": "https://n8n.test", "N8N_TIMEOUT_SECONDS": "2.5"}).timeout_seconds == 2.5
