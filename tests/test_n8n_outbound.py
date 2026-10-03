from copy import deepcopy
from hashlib import sha256
import json

import pytest

import httpx

from jobtrail_ai_scorer.n8n_outbound import (
    DeliveryResult,
    N8nConfig,
    N8nOutboundAdapter,
    build_envelope,
)


def _v2_envelope():
    from jobtrail_ai_scorer.opportunity_cards import serialize_cards
    from jobtrail_ai_scorer.opportunity_intelligence import (
        PublicJobIdentity, OpportunityResult, Citation, CompanyClaim, CompanyBrief,
    )
    job = PublicJobIdentity('Acme', 'Engineer', 'Remote', 'https://source.test/1', 'indeed', '1')
    proof = Citation('https://acme.test/about',
                     'Software company (https://acme.test/about?lang=en).',
                     '2026-06-01T00:00:00+00:00', 'company_reported')
    cards = serialize_cards([(job, OpportunityResult(
        'verified', job.original_url, official_url='https://acme.test/jobs/1',
        checked_at=proof.checked_at, citations=(proof,),
        company_brief=CompanyBrief('available', (CompanyClaim('sector', 'Software', proof),)),
    ))])
    return build_envelope(
        run_id='run-1', occurred_at='2026-06-01T00:00:00+00:00', searched=1,
        imported=1, scored=1, selected={'source': 'indeed', 'sourceJobId': '1',
                                      'score': 91, 'evidence': [{'label': 'direct', 'text': 'CV match'}]},
        opportunities=cards, feedback_actions=True,
    )


@pytest.mark.parametrize('field', ['original_url', 'official_url', 'proof_url'])
@pytest.mark.parametrize('url', [
    'https://acme.test/?token=synthetic', 'https://user:pass@acme.test/',
    'https://acme.test/?%2561pi_key=synthetic',
    'https://acme.test/#token=synthetic', 'https://127.0.0.1/', 'http://acme.test/',
])
def test_v2_denies_unsafe_url_mappings_before_http(field, url):
    envelope = _v2_envelope()
    card = envelope['opportunities'][0]
    if field == 'proof_url':
        card['citations'][0]['url'] = url
    else:
        card[field] = url
    calls = []
    adapter = N8nOutboundAdapter(N8nConfig(enabled=True, endpoint='https://n8n.test/hook'),
        transport=httpx.MockTransport(lambda request: (calls.append(request) or httpx.Response(202))))
    assert adapter.send(envelope) == DeliveryResult('failed', 0, '', 'terminal')
    assert calls == []


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


def test_result_counters_are_clamped_to_explicit_maximum():
    envelope = build_envelope(
        run_id="run-1", occurred_at="now", searched=10**30,
        imported=10**30, scored=10**30, failures=("x",) * 100,
    )
    assert envelope["result"]["searched"] == 1_000_000
    assert envelope["result"]["imported"] == 1_000_000
    assert envelope["result"]["scored"] == 1_000_000
    assert envelope["result"]["failure_count"] == 5


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
    assert envelope["result"]["failure_count"] == 1
    assert len(envelope["selected"]["title"]) == 200


def test_envelope_bounds_and_redacts_timestamp_and_failure_labels():
    envelope = build_envelope(
        run_id="run-1",
        occurred_at="PROMPT_SENTINEL-" + "x" * 300,
        searched=0,
        imported=0,
        scored=0,
        failures=("PROMPT_SENTINEL?token=secret&keep=yes", "ok"),
    )

    assert "PROMPT_SENTINEL" not in envelope["occurred_at"]
    assert len(envelope["occurred_at"]) <= 200
    assert envelope["result"]["failures"] == ["[redacted]?keep=yes", "ok"]
    assert envelope["result"]["failure_count"] == 2


def test_long_identity_values_do_not_collide_after_output_clipping():
    prefix = "x" * 200
    first = build_envelope(
        run_id=prefix + "-one", occurred_at="now", searched=1, imported=1, scored=1,
        selected={"source": "source", "sourceJobId": prefix + "-job-one"},
        feedback_actions=True,
    )
    second = build_envelope(
        run_id=prefix + "-two", occurred_at="now", searched=1, imported=1, scored=1,
        selected={"source": "source", "sourceJobId": prefix + "-job-two"},
        feedback_actions=True,
    )
    assert first["run_id"] == second["run_id"]
    assert first["selected"]["sourceJobId"] == second["selected"]["sourceJobId"]
    assert first["event_id"] != second["event_id"]
    assert first["actions"][0]["action_id"] != second["actions"][0]["action_id"]


def test_delimiter_distinct_raw_identities_produce_distinct_event_and_action_ids():
    first = build_envelope(
        run_id="run|a", occurred_at="now", searched=1, imported=1, scored=1,
        selected={"source": "source", "sourceJobId": "job"}, feedback_actions=True,
    )
    second = build_envelope(
        run_id="run", occurred_at="now", searched=1, imported=1, scored=1,
        selected={"source": "a|source", "sourceJobId": "job"}, feedback_actions=True,
    )
    assert first["event_id"] != second["event_id"]
    assert first["actions"][0]["action_id"] != second["actions"][0]["action_id"]
    assert first == build_envelope(
        run_id="run|a", occurred_at="now", searched=1, imported=1, scored=1,
        selected={"source": "source", "sourceJobId": "job"}, feedback_actions=True,
    )


def test_envelope_is_versioned_bounded_allowlisted_and_stable():
    first = _envelope()
    second = _envelope()
    assert first == second
    assert set(first) == {"schema_version", "event_id", "run_id", "event_type", "occurred_at", "result", "selected"}
    assert first["schema_version"] == 1
    assert first["event_id"] == sha256(
        b'{"job_id":"1","run_id":"run-1","source":"indeed","version":"jobtrail-n8n-v1"}'
    ).hexdigest()[:32]
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


def test_adapter_rejects_unsafe_mapping_without_sending():
    calls = []
    transport = httpx.MockTransport(lambda request: (calls.append(request) or httpx.Response(202)))
    unsafe = dict(_envelope())
    unsafe["unexpected"] = "must not cross the boundary"
    result = N8nOutboundAdapter(
        N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport,
    ).send(unsafe)
    assert result == DeliveryResult("failed", 0, "", "terminal")
    assert calls == []


def test_adapter_rejects_forbidden_caller_text_before_http():
    calls = []
    transport = httpx.MockTransport(lambda request: (calls.append(request) or httpx.Response(202)))
    for field, value in (
        ("run_id", "PROMPT_SENTINEL caller input"),
        ("occurred_at", "CREDENTIAL_SENTINEL caller input"),
    ):
        unsafe = dict(_envelope(), **{field: value})
        result = N8nOutboundAdapter(
            N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport,
        ).send(unsafe)
        assert result == DeliveryResult("failed", 0, "", "terminal")
    unsafe = dict(_envelope())
    unsafe["result"] = dict(unsafe["result"], failures=["PROFILE_SENTINEL leaked"])
    result = N8nOutboundAdapter(
        N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport,
    ).send(unsafe)
    assert result == DeliveryResult("failed", 0, "", "terminal")
    assert calls == []


def test_adapter_rejects_malformed_and_valid_looking_mutated_actions_without_sending():
    calls = []
    transport = httpx.MockTransport(lambda request: (calls.append(request) or httpx.Response(202)))
    valid = build_envelope(
        run_id="run-1", occurred_at="2025-01-01T00:00:00+00:00", searched=1,
        imported=1, scored=1, selected={"source": "indeed", "sourceJobId": "job-1"},
        feedback_actions=True,
    )
    for field, value in (
        ("action_id", "A" * 32),
        ("token_id", "f" * 31),
        ("expires_at", "not-an-iso-timestamp"),
    ):
        unsafe = dict(valid)
        unsafe["actions"] = [dict(valid["actions"][0], **{field: value})]
        result = N8nOutboundAdapter(
            N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport,
        ).send(unsafe)
        assert result == DeliveryResult("failed", 0, "", "terminal")
    for expires_at in ("2024-12-31T23:59:59+00:00", "2025-01-03T00:00:00+00:00"):
        unsafe = dict(valid)
        unsafe["actions"] = [dict(valid["actions"][0], expires_at=expires_at)]
        result = N8nOutboundAdapter(
            N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport,
        ).send(unsafe)
        assert result == DeliveryResult("failed", 0, "", "terminal")
    # A different, valid-looking hexadecimal id is still rejected because it
    # is not canonically bound to this event and action type.
    unsafe = dict(valid)
    unsafe["actions"] = [dict(valid["actions"][0], action_id="a" * 32)]
    assert N8nOutboundAdapter(
        N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport,
    ).send(unsafe).classification == "terminal"
    assert calls == []


def test_adapter_requires_exact_canonical_action_set_before_http():
    calls = []
    transport = httpx.MockTransport(lambda request: (calls.append(request) or httpx.Response(202)))
    valid = build_envelope(
        run_id="run-1", occurred_at="2025-01-01T00:00:00+00:00", searched=1,
        imported=1, scored=1, selected={"source": "indeed", "sourceJobId": "job-1"},
        feedback_actions=True,
    )
    canonical = valid["actions"]
    invalid_sets = (
        [],
        canonical[:2],
        [canonical[0], canonical[0], canonical[2]],
        [dict(canonical[0], action="unknown"), canonical[1], canonical[2]],
        [canonical[1], canonical[0], canonical[2]],
    )
    adapter = N8nOutboundAdapter(
        N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport,
    )
    for actions in invalid_sets:
        unsafe = dict(valid, actions=actions)
        assert adapter.send(unsafe) == DeliveryResult("failed", 0, "", "terminal")
    assert calls == []

    assert adapter.send(dict(valid, actions=list(canonical))) == DeliveryResult(
        "accepted", 1, valid["event_id"],
    )
    assert len(calls) == 1


def test_adapter_acknowledges_2xx_and_reuses_event_id_on_retry():
    requests = []
    transport = httpx.MockTransport(lambda request: (requests.append(request) or httpx.Response(202)))
    adapter = N8nOutboundAdapter(N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport)
    envelope = _envelope()
    result = adapter.send(envelope)
    assert result == DeliveryResult("accepted", 1, envelope["event_id"])
    import json
    sent = json.loads(requests[0].content)
    assert sent == envelope
    assert "PRIVATE" not in json.dumps(sent)


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
    result = adapter.send(_envelope())
    assert result.status == "accepted"
    assert len(attempts) == 3
    import json
    assert {json.loads(request.content)["event_id"] for request in attempts} == {_envelope()["event_id"]}

    terminal_attempts = []
    adapter = N8nOutboundAdapter(
        N8nConfig(enabled=True, endpoint="https://n8n.test/hook"),
        transport=httpx.MockTransport(lambda request: (terminal_attempts.append(request) or httpx.Response(400))),
    )
    assert adapter.send(_envelope()).status == "failed"
    assert adapter.send(_envelope()).classification == "terminal"
    assert len(terminal_attempts) == 2


def test_timeout_retries_and_exhaustion_are_uncertain():
    attempts = []

    def timeout(_request):
        attempts.append(True)
        raise httpx.ReadTimeout("timed out")

    adapter = N8nOutboundAdapter(
        N8nConfig(enabled=True, endpoint="https://n8n.test/hook", retry_attempts=3),
        transport=httpx.MockTransport(timeout), sleep=lambda _: None,
    )
    result = adapter.send(_envelope())
    assert result == DeliveryResult("failed", 3, _envelope()["event_id"], "uncertain")
    assert len(attempts) == 3


def test_5xx_exhaustion_is_exhausted():
    attempts = []
    adapter = N8nOutboundAdapter(
        N8nConfig(enabled=True, endpoint="https://n8n.test/hook", retry_attempts=2),
        transport=httpx.MockTransport(lambda request: (attempts.append(request) or httpx.Response(503))),
        sleep=lambda _: None,
    )
    result = adapter.send(_envelope())
    assert result == DeliveryResult("failed", 2, _envelope()["event_id"], "exhausted")
    assert len(attempts) == 2


def test_disabled_missing_endpoint_and_dry_run_never_send():
    calls = []
    transport = httpx.MockTransport(lambda request: (calls.append(request) or httpx.Response(204)))
    envelope = _envelope()
    for config in (N8nConfig(), N8nConfig(enabled=True, endpoint="")):
        result = N8nOutboundAdapter(config, transport=transport).send(envelope)
        assert result.status == "disabled"
    assert N8nOutboundAdapter(N8nConfig(enabled=True, endpoint="https://n8n.test/hook"), transport=transport).send(envelope, dry_run=True).status == "disabled"
    assert calls == []


def test_direct_config_rejects_invalid_timeout_and_retry_attempts():
    import pytest

    assert N8nConfig(timeout_seconds=1).timeout_seconds == 1
    assert N8nConfig(timeout_seconds=60).timeout_seconds == 60
    for kwargs in (
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": float("-inf")},
        {"timeout_seconds": 60.0001},
        {"timeout_seconds": True},
        {"retry_attempts": True},
        {"retry_attempts": 1.0},
        {"retry_attempts": 0},
        {"retry_attempts": 4},
    ):
        with pytest.raises(ValueError):
            N8nConfig(**kwargs)


def test_config_is_disabled_by_default_and_parses_bounded_values():
    config = N8nConfig.from_env({})
    assert config.enabled is False
    assert config.endpoint == ""
    assert N8nConfig.from_env({"N8N_ENABLED": "1", "N8N_ENDPOINT": "https://n8n.test", "N8N_TIMEOUT_SECONDS": "2.5"}).timeout_seconds == 2.5
    assert N8nConfig.from_env({"N8N_ENABLED": "1", "N8N_TIMEOUT_SECONDS": "60"}).timeout_seconds == 60


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

    for key, value in (
        ("N8N_TIMEOUT_SECONDS", "nope"),
        ("N8N_TIMEOUT_SECONDS", "nan"),
        ("N8N_TIMEOUT_SECONDS", "inf"),
        ("N8N_TIMEOUT_SECONDS", "-inf"),
        ("N8N_TIMEOUT_SECONDS", "60.0001"),
        ("N8N_RETRY_ATTEMPTS", "0"),
        ("N8N_RETRY_ATTEMPTS", "4"),
        ("N8N_RETRY_ATTEMPTS", "1.5"),
        ("N8N_RETRY_ATTEMPTS", True),
        ("N8N_RETRY_ATTEMPTS", 1.5),
    ):
        with pytest.raises((ValueError, TypeError)):
            N8nConfig.from_env({"N8N_ENABLED": "1", key: value})


def test_feedback_actions_are_omitted_without_both_identity_fields():
    for selected in ({"source": "indeed"}, {"sourceJobId": "job-1"}, {"source": "  ", "sourceJobId": "job-1"}):
        envelope = build_envelope(
            run_id="run-1", occurred_at="2025-01-01T00:00:00+00:00",
            searched=1, imported=1, scored=1, selected=selected,
            feedback_actions=True,
        )
        assert "actions" not in envelope


def test_feedback_actions_are_opt_in_bounded_and_opaque():
    envelope = build_envelope(
        run_id="run-1",
        occurred_at="2025-01-01T00:00:00+00:00",
        searched=1,
        imported=1,
        scored=1,
        selected={
            "title": "Title",
            "source": "indeed",
            "sourceJobId": "job-1",
            "fit_score": 91,
            "coverage_score": 88,
            "classification": "APPLY",
            "evidence": ["direct", "equivalent", "PROMPT_SENTINEL"],
            "gaps": ["missing"] * 20,
        },
        feedback_actions=True,
    )
    assert {item["action"] for item in envelope["actions"]} == {"applied", "dismissed", "interesting"}
    assert len(envelope["actions"]) == 3
    for item in envelope["actions"]:
        assert set(item) == {"action", "action_id", "token_id", "expires_at"}
        assert len(item["action_id"]) == 32
        assert len(item["token_id"]) == 32
        assert item["expires_at"] == "2025-01-02T00:00:00+00:00"
        assert "PROMPT_SENTINEL" not in str(item)
    assert envelope["actions"] == build_envelope(
        run_id="run-1", occurred_at="2025-01-01T00:00:00+00:00", searched=1,
        imported=1, scored=1, selected={"source": "indeed", "sourceJobId": "job-1"},
        feedback_actions=True,
    )["actions"]


def test_non_finite_optional_scores_are_rejected_while_finite_scores_are_clipped():
    for field in ("score", "fit_score", "coverage_score"):
        for value in (float("nan"), float("inf"), float("-inf")):
            selected = build_envelope(
                run_id="run-1", occurred_at="now", searched=1, imported=1, scored=1,
                selected={field: value},
            )["selected"]
            assert field not in selected

    selected = build_envelope(
        run_id="run-1", occurred_at="now", searched=1, imported=1, scored=1,
        selected={"score": 10**1000, "fit_score": -1, "coverage_score": 50},
    )["selected"]
    assert selected == {"score": 100, "fit_score": 0, "coverage_score": 50}


def test_optional_scores_and_labels_are_bounded_and_sanitized():
    selected = build_envelope(
        run_id="run-1", occurred_at="now", searched=1, imported=1, scored=1,
        selected={
            "score": 999, "fit_score": 999, "coverage_score": -4, "classification": "C" * 300,
            "strengths": ["s" * 300, "PROMPT_SENTINEL"],
            "evidence": [{"label": "direct", "text": "x" * 300},
                         {"label": "inferred", "text": "PROMPT_SENTINEL"}] * 20,
            "gap_labels": ["g" * 300],
        },
    )["selected"]
    assert selected["score"] == 100
    assert selected["fit_score"] == 100
    assert selected["coverage_score"] == 0
    assert len(selected["classification"]) == 200
    assert len(selected["strengths"]) == 2
    assert len(selected["strengths"][0]) == 200
    assert selected["strengths"][1] == "[redacted]"
    assert len(selected["evidence"]) == 5
    assert len(selected["evidence"][0]["text"]) == 200
    assert selected["evidence"][1] == {"label": "inferred", "text": "[redacted]"}
    assert len(selected["gap_labels"][0]) == 200


def test_structured_evidence_drops_malformed_entries_and_unknown_fields():
    envelope = build_envelope(
        run_id="synthetic-run", occurred_at="2030-01-01T00:00:00+00:00",
        searched=1, imported=1, scored=1,
        selected={"evidence": [
            {"label": "direct", "text": "https://example.test/?token=synthetic&keep=yes", "unknown": "ignored"},
            {"label": "unknown", "text": "bad label"},
            {"label": ["direct"], "text": "bad label type"},
            {"label": "missing", "text": " "},
            "not structured",
            {"label": "equivalent", "text": "beyond bound"},
        ]},
    )
    assert envelope["selected"]["evidence"] == [{
        "label": "direct", "text": "https://example.test/?keep=yes",
    }]
    requests = []
    adapter = N8nOutboundAdapter(
        N8nConfig(enabled=True, endpoint="https://synthetic.test/hook"),
        transport=httpx.MockTransport(lambda request: (requests.append(request) or httpx.Response(200))),
    )
    envelope["selected"]["evidence"][0]["unknown"] = "invalid envelope"
    assert adapter.send(envelope).status == "failed"
    assert requests == []


def test_supplied_run_id_is_redacted_and_clipped():
    envelope = build_envelope(
        run_id="PROMPT_SENTINEL-" + "r" * 300, occurred_at="now",
        searched=0, imported=0, scored=0,
    )
    assert "PROMPT_SENTINEL" not in envelope["run_id"]
    assert len(envelope["run_id"]) <= 200


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


def test_v2_public_quotes_selected_compatibility_and_retry_identity():
    envelope = _v2_envelope()
    assert envelope['schema_version'] == 2
    assert envelope['selected'] == {'source': 'indeed', 'sourceJobId': '1', 'score': 91}
    assert envelope['event_id'] == sha256(
        b'{"job_id":"1","run_id":"run-1","source":"indeed","version":"jobtrail-n8n-v2"}'
    ).hexdigest()[:32]
    assert envelope == _v2_envelope()
    attempts = []
    def respond(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(503 if len(attempts) < 3 else 202)
    adapter = N8nOutboundAdapter(N8nConfig(enabled=True, endpoint='https://n8n.test/hook'),
        transport=httpx.MockTransport(respond), sleep=lambda _: None)
    assert adapter.send(envelope) == DeliveryResult('accepted', 3, envelope['event_id'])
    assert attempts == [envelope] * 3
    proof = attempts[0]['opportunities'][0]['citations'][0]
    assert proof['excerpt'] == 'Software company (https://acme.test/about?lang=en).'
    assert proof['retrieved_at'] == '2026-06-01T00:00:00+00:00'
    assert envelope['actions'][0]['expires_at'] == '2026-06-02T00:00:00+00:00'


@pytest.mark.parametrize('mutation', [
    'missing', 'empty', 'four', 'tuple', 'unknown_top', 'v1_cards', 'bool_version',
    'string_version', 'unknown_version', 'unknown_card', 'nested_title', 'unknown_status',
    'missing_official', 'inconsistent_official', 'missing_claims', 'four_claims',
    'four_proofs', 'bad_date', 'naive_date', 'date_only', 'unknown_proof', 'nested_quote',
    'unknown_claim', 'bad_field', 'bad_attribution', 'nested_value', 'forbidden_quote',
    'inline_secret_quote', 'inline_fragment_secret', 'cv_evidence',
])
def test_v2_closed_schema_rejects_mutations_before_http(mutation):
    envelope = _v2_envelope()
    card = envelope['opportunities'][0]
    proof = card['citations'][0]
    claim = card['claims'][0]
    if mutation == 'missing': del envelope['opportunities']
    elif mutation == 'empty': envelope['opportunities'] = []
    elif mutation == 'four': envelope['opportunities'] *= 4
    elif mutation == 'tuple': envelope['opportunities'] = tuple(envelope['opportunities'])
    elif mutation == 'unknown_top': envelope['profile'] = 'private'
    elif mutation == 'v1_cards': envelope['schema_version'] = 1
    elif mutation == 'bool_version': envelope['schema_version'] = True
    elif mutation == 'string_version': envelope['schema_version'] = '2'
    elif mutation == 'unknown_version': envelope['schema_version'] = 3
    elif mutation == 'unknown_card': card['reasoning'] = 'private'
    elif mutation == 'nested_title': card['title'] = {'profile': 'private'}
    elif mutation == 'unknown_status': card['status'] = 'success'
    elif mutation == 'missing_official': del card['official_url']
    elif mutation == 'inconsistent_official': card['status'] = 'unverified'
    elif mutation == 'missing_claims': del card['claims']
    elif mutation == 'four_claims': card['claims'] *= 4
    elif mutation == 'four_proofs': card['citations'] *= 4
    elif mutation == 'bad_date': proof['retrieved_at'] = 'not a date'
    elif mutation == 'naive_date': proof['retrieved_at'] = '2026-06-01T00:00:00'
    elif mutation == 'date_only': proof['retrieved_at'] = '2026-06-01'
    elif mutation == 'unknown_proof': proof['notes'] = 'private'
    elif mutation == 'nested_quote': proof['excerpt'] = {'text': 'nested'}
    elif mutation == 'unknown_claim': claim['description'] = 'private'
    elif mutation == 'bad_field': claim['field'] = 'culture'
    elif mutation == 'bad_attribution': claim['attribution'] = 'verified_fact'
    elif mutation == 'nested_value': claim['value'] = ['nested']
    elif mutation == 'forbidden_quote': proof['excerpt'] = 'PROFILE_SENTINEL'
    elif mutation == 'inline_secret_quote': proof['excerpt'] = 'See https%3A%2F%2Facme.test%2F%3Ftoken%3Dsynthetic'
    elif mutation == 'inline_fragment_secret': proof['excerpt'] = 'See https://acme.test/#token=synthetic'
    elif mutation == 'cv_evidence': envelope['selected']['evidence'] = [{'label': 'direct', 'text': 'private'}]
    calls = []
    adapter = N8nOutboundAdapter(N8nConfig(enabled=True, endpoint='https://n8n.test/hook'),
        transport=httpx.MockTransport(lambda request: (calls.append(request) or httpx.Response(202))))
    assert adapter.send(envelope) == DeliveryResult('failed', 0, '', 'terminal')
    assert calls == []


def test_v2_builder_denies_empty_oversized_and_unsafe_cards():
    valid = _v2_envelope()['opportunities']
    unsafe = deepcopy(valid)
    unsafe[0]['profile'] = 'private'
    for cards in ([], valid * 4, unsafe):
        with pytest.raises(ValueError):
            build_envelope(run_id='run', occurred_at='now', searched=0, imported=0,
                           scored=0, opportunities=cards)


def test_empty_selected_normalized_to_null():
    # Empty selected with v2 (opportunities present) must emit selected=null, not selected={}.
    cards = _v2_envelope()['opportunities']
    for selected in ({}, {'title': None, 'score': None}):
        envelope = build_envelope(
            run_id='run-1', occurred_at='2026-06-01T00:00:00+00:00',
            searched=1, imported=1, scored=1, selected=selected,
            opportunities=cards,
        )
        assert envelope['schema_version'] == 2
        assert envelope['selected'] is None, f"expected null, got {envelope['selected']!r}"


def test_canonical_wrapper_exact_v1_and_three_card_cap():
    from jobtrail_ai_scorer.automation import AutomationRun, build_n8n_envelope
    from jobtrail_ai_scorer.opportunity_intelligence import OpportunityResult
    selected = {'company': 'Acme', 'title': 'Engineer', 'location': 'Remote',
                'jobUrl': 'https://source.test/1', 'source': 'indeed', 'sourceJobId': '1',
                'evidence': [{'label': 'direct', 'text': 'legacy'}]}
    kwargs = dict(occurred_at='2026-06-01T00:00:00+00:00', feedback_actions=True)
    run = AutomationRun(searched=4, scored=4, selected=selected, run_id='run-1',
                        opportunities=(selected,) * 4)
    legacy = build_envelope(run_id='run-1', searched=4, imported=0, scored=4,
                            selected=selected, **kwargs)
    assert build_n8n_envelope(run, **kwargs) == legacy
    from dataclasses import replace
    enriched = replace(run, intelligence=(OpportunityResult('unverified', selected['jobUrl']),) * 4)
    envelope = build_n8n_envelope(enriched, **kwargs)
    assert envelope['schema_version'] == 2 and len(envelope['opportunities']) == 3
    assert envelope['selected']['sourceJobId'] == legacy['selected']['sourceJobId']
    assert 'evidence' not in envelope['selected']
    assert 'evidence' in legacy['selected']
    # Intelligence without a valid public identity must never emit empty v2.
    assert build_n8n_envelope(replace(run, opportunities=(), intelligence=enriched.intelligence), **kwargs) == legacy
