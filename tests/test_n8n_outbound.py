from hashlib import sha256

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


def test_failure_labels_are_clipped_to_128_while_selected_text_allows_200():
    envelope = build_envelope(
        run_id="run-1",
        occurred_at="2025-01-01T00:00:00+00:00",
        searched=0,
        imported=0,
        scored=0,
        failures=("f" * 300,),
        selected={"title": "T" * 300},
    )

    assert len(envelope["result"]["failures"][0]) == 128
    assert len(envelope["selected"]["title"]) == 200


def test_envelope_is_versioned_bounded_allowlisted_and_stable():
    first = _envelope()
    second = _envelope()
    assert first == second
    assert set(first) == {"schema_version", "event_id", "run_id", "event_type", "occurred_at", "result", "selected"}
    assert first["schema_version"] == 1
    assert first["event_id"] == sha256(b"jobtrail-n8n-v1|run-1|indeed|1").hexdigest()[:32]
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
    import json
    assert json.loads(requests[0].content)["event_id"] == envelope["event_id"]


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


def test_disabled_config_ignores_malformed_tuning_values():
    config = N8nConfig.from_env({
        "N8N_ENABLED": "0",
        "N8N_TIMEOUT_SECONDS": "not-a-number",
        "N8N_RETRY_ATTEMPTS": "also-not-a-number",
    })
    assert config.enabled is False
    assert config.timeout_seconds == 5.0
    assert config.retry_attempts == 3


def test_enabled_config_rejects_malformed_or_unbounded_tuning_values():
    import pytest

    for key, value in (("N8N_TIMEOUT_SECONDS", "nope"), ("N8N_RETRY_ATTEMPTS", "0"), ("N8N_RETRY_ATTEMPTS", "4")):
        with pytest.raises((ValueError, TypeError)):
            N8nConfig.from_env({"N8N_ENABLED": "1", key: value})


def test_selected_summary_rejects_nested_values_and_redacts_strings_and_urls():
    envelope = build_envelope(
        run_id="run-1", occurred_at="now", searched=1, imported=1, scored=1,
        selected={
            "title": "/DATA/ RESUME_SENTINEL " + "T" * 300,
            "company": {"secret": "nested"},
            "score": {"value": 99},
            "recommendation": ["APPLY"],
            "jobUrl": "https://jobs.test/1?token=abc&keep=yes&api_key=def",
            "jobTrailLink": "https://trail.test/1?access_token=ghi&password=jkl&client_secret=mno",
            "source": "source",
            "sourceJobId": "id",
        },
    )
    selected = envelope["selected"]
    assert selected["title"].startswith("[redacted] [redacted]")
    assert len(selected["title"]) == 200
    assert "company" not in selected
    assert "score" not in selected
    assert "recommendation" not in selected
    assert selected["jobUrl"] == "https://jobs.test/1?keep=yes"
    assert selected["jobTrailLink"] == "https://trail.test/1"
