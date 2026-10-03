import httpx
from urllib.parse import quote

import pytest

from jobtrail_ai_scorer.notify import FORBIDDEN_TOKENS
from jobtrail_ai_scorer.public_search import MAX_RESPONSE_BYTES

from jobtrail_ai_scorer.public_search import (
    BravePublicSearchAdapter, PublicSearchConfig, PublicSearchRequest, _candidate_url,
)


def test_public_fields_become_fixed_brave_request_and_unverified_lead():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"web": {"results": [{
            "title": "Vacancy", "url": "https://employer.example/jobs/1",
            "description": "Synthetic snippet",
        }]}})

    adapter = BravePublicSearchAdapter(
        PublicSearchConfig(enabled=True, api_key="fake-test-key"),
        transport=httpx.MockTransport(handler),
    )
    result = adapter.search(PublicSearchRequest("Example Co", "Engineer", "Remote"))
    assert result.status == "found"
    assert len(calls) == 1
    assert str(calls[0].url).startswith("https://api.search.brave.com/res/v1/web/search?")
    assert calls[0].url.params["q"] == '"Example Co" "Engineer" "Remote" jobs'
    assert calls[0].url.params["count"] == "5"
    assert calls[0].headers["X-Subscription-Token"] == "fake-test-key"
    assert result.leads[0].source_url == "https://employer.example/jobs/1"
    assert result.leads[0].snippet == "Synthetic snippet"
    assert result.leads[0].verified is False


REQUEST = PublicSearchRequest("Example", "Engineer", "Remote")


def adapter_for(handler, config=None):
    return BravePublicSearchAdapter(
        config or PublicSearchConfig(enabled=True, api_key="fake-test-key"),
        transport=httpx.MockTransport(handler),
    )


def member(url="https://employer.example/jobs/1", **overrides):
    return {"title": "Vacancy", "url": url, "description": "Synthetic", **overrides}


def response(items):
    return httpx.Response(200, json={"web": {"results": items}})


@pytest.mark.parametrize("config,dry_run,status", [
    (PublicSearchConfig(), False, "disabled"),
    (PublicSearchConfig(enabled=True), False, "provider_unconfigured"),
    (PublicSearchConfig(enabled=True, api_key="fake-test-key"), True, "dry_run"),
])
def test_gates_make_zero_http_attempts(config, dry_run, status):
    def forbidden(request):
        pytest.fail("HTTP forbidden")
    assert adapter_for(forbidden, config).search(REQUEST, dry_run=dry_run).status == status


@pytest.mark.parametrize("value", [
    "", " " * 3, "a" * 201, "site:evil.example", 'Co" OR secrets',
    "https://evil.example", "Co\nprivate", "Co\tprivate", {}, None,
    *FORBIDDEN_TOKENS, *(s.lower() for s in FORBIDDEN_TOKENS),
])
def test_rejects_nonpublic_or_query_injecting_fields(value):
    with pytest.raises(ValueError, match="invalid public search field"):
        PublicSearchRequest("Example", value, "Remote")


def test_schema_is_closed_and_normalized():
    with pytest.raises(TypeError):
        PublicSearchRequest("Example", "Engineer", "Remote", description="private")
    with pytest.raises(TypeError):
        adapter_for(lambda r: response([])).search({"company": "Example"})
    assert PublicSearchRequest(" Example  Co ", "C++ Engineer", "México").query == (
        '\"Example Co\" \"C++ Engineer\" \"México\" jobs'
    )


@pytest.mark.parametrize("timeout", [0, -1, 31, float("nan"), float("inf"), True, "5"])
def test_timeout_is_finite_positive_bounded(timeout):
    with pytest.raises(ValueError, match="invalid search timeout"):
        PublicSearchConfig(timeout_seconds=timeout)


def test_explicit_environment_contract_and_secret_repr():
    assert PublicSearchConfig.from_env({}) == PublicSearchConfig()
    config = PublicSearchConfig.from_env({
        "OPPORTUNITY_INTELLIGENCE_ENABLED": "1", "BRAVE_SEARCH_API_KEY": "fake-test-key",
    })
    assert config.enabled and config.api_key == "fake-test-key"
    assert "fake-test-key" not in repr(config)
    assert PublicSearchConfig.from_env({"BRAVE_SEARCH_API_KEY": "fake-test-key"}).api_key is None
    assert PublicSearchConfig(enabled=True, api_key="  ").api_key is None
    with pytest.raises(ValueError):
        PublicSearchConfig.from_env({"OPPORTUNITY_INTELLIGENCE_ENABLED": "maybe"})
    with pytest.raises(ValueError, match="invalid search credential"):
        PublicSearchConfig(api_key="fake\nheader")


@pytest.mark.parametrize("code,status", [
    (400, "provider_error"), (401, "provider_error"), (403, "provider_error"),
    (429, "quota_exceeded"), (500, "unavailable"), (503, "unavailable"),
    (302, "bad_response"),
])
def test_statuses_without_retry_redirect_or_raw_body_leak(code, status, caplog, capsys):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(code, text="fake-test-key private provider body",
                              headers={"Location": "https://evil.example"})
    adapter = adapter_for(handler)
    for _ in range(3):
        result = adapter.search(REQUEST)
        assert result.status == status and not result.leads
        assert "fake-test-key" not in repr(result)
    assert adapter.search(REQUEST).status == "budget_exhausted"
    assert len(calls) == 3
    assert "private provider body" not in caplog.text
    assert "fake-test-key" not in caplog.text
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("error,status", [
    (httpx.ReadTimeout("fake-test-key"), "timeout"),
    (httpx.ConnectError("fake-test-key"), "unavailable"),
])
def test_transport_failure_consumes_attempt_budget(error, status):
    calls = []
    def handler(request):
        calls.append(request)
        raise error
    adapter = adapter_for(handler)
    assert [adapter.search(REQUEST).status for _ in range(4)] == [status] * 3 + ["budget_exhausted"]
    assert len(calls) == 3
    assert adapter_for(lambda r: response([])).search(REQUEST).status == "not_found"


@pytest.mark.parametrize("payload", [None, [], {}, {"web": []}, {"web": {}},
    {"web": {"results": {}}}])
def test_malformed_schema_is_not_success(payload):
    assert adapter_for(lambda r: httpx.Response(200, json=payload)).search(REQUEST).status == "bad_response"


def test_bad_json_and_compressed_response():
    for data, headers in [(b"not json", {}), (b"{}", {"Content-Encoding": "unknown"})]:
        assert adapter_for(lambda r: httpx.Response(200, content=data, headers=headers)).search(REQUEST).status == "bad_response"


class Chunks(httpx.SyncByteStream):
    def __init__(self):
        self.read_chunks = 0
    def __iter__(self):
        for _ in range(1000):
            self.read_chunks += 1
            yield b"x" * 8192


def test_stream_is_bounded_before_json_parsing():
    stream = Chunks()
    result = adapter_for(lambda r: httpx.Response(200, stream=stream)).search(REQUEST)
    assert result.status == "oversized_response"
    assert stream.read_chunks == MAX_RESPONSE_BYTES // 8192 + 1


@pytest.mark.parametrize("url", [
    "http://employer.example/job", "javascript:alert(1)", "relative/job", None,
    "https://user:password@employer.example/", "https://localhost/",
    "https://sub.localhost/", "https://localhost.localdomain/", "https://employer.local/", "https://metadata.internal/",
    "https://224.0.0.1/", "https://[ff02::1]/",
    "https://127.0.0.1/", "https://10.0.0.1/", "https://169.254.169.254/",
    "https://[::1]/", "https://[fd00::1]/", "https://2130706433/",
    "https://127.1/", "https://employer.example:8443/", "https://employer.example:bad/",
    "https://employer.example/\\evil", "https://employer.example/%0aevil",
    "https://employer.example/?token=fake", "https://employer.example/" + "a" * 2048,
])
def test_unsafe_candidate_url_is_discarded_without_fetching(url):
    calls = []
    def handler(request):
        calls.append(request)
        return response([member(url)])
    result = adapter_for(handler).search(REQUEST)
    assert result.status == "untrusted" and not result.leads
    assert len(calls) == 1


@pytest.mark.parametrize("key", [
    "token", "api_key", "apikey", "access_token", "password", "client_secret",
    "secret", "app_id", "app_key",
])
@pytest.mark.parametrize("encoding", ["plain", "uppercase", "percent_encoded"])
@pytest.mark.parametrize("value", ["synthetic-credential", ""])
def test_credential_query_candidate_is_rejected_without_diagnostic_leaks(
    key, encoding, value, caplog, capsys,
):
    if encoding == "uppercase":
        key = key.upper()
    elif encoding == "percent_encoded":
        key = "".join(f"%{ord(char):02X}" for char in key.upper())
    url = f"https://employer.example/jobs/1?utm_source=public&{key}={value}"
    calls = []

    def handler(request):
        calls.append(request)
        return response([member(url)])

    result = adapter_for(handler).search(REQUEST)
    assert result.status == "untrusted"
    assert not result.leads
    assert len(calls) == 1
    captured = capsys.readouterr()
    diagnostics = repr(result) + caplog.text + captured.out + captured.err
    assert "synthetic-credential" not in diagnostics
    assert "employer.example" not in diagnostics


def test_public_tracking_query_is_preserved_when_credential_candidate_is_rejected():
    public_url = "https://employer.example/jobs/1?utm_source=public&ref=search&campaign="
    result = adapter_for(lambda r: response([
        member("https://employer.example/jobs/2?client_secret=synthetic-credential"),
        member(public_url),
    ])).search(REQUEST)
    assert result.status == "found"
    assert [lead.source_url for lead in result.leads] == [public_url]


def test_malformed_members_bounded_iteration_and_secret_echo():
    items = [None, {}, member(description=23), member(title="fake-test-key"),
             member(description="PROFILE_SENTINEL"), member()]
    assert adapter_for(lambda r: response(items)).search(REQUEST).status == "untrusted"
    assert adapter_for(lambda r: response([member(title="x" * 301)])).search(REQUEST).status == "untrusted"
    assert adapter_for(lambda r: response([member(description="x" * 2001)])).search(REQUEST).status == "untrusted"


def test_global_dedup_and_candidate_limit_and_new_run_reset():
    calls = []
    def handler(request):
        calls.append(request)
        start = (len(calls) - 1) * 4
        return response([member(f"https://employer.example/jobs/{i}#fragment")
                         for i in range(start, start + 6)])
    adapter = adapter_for(handler)
    batches = [adapter.search(REQUEST) for _ in range(3)]
    assert [len(b.leads) for b in batches] == [5, 4, 1]
    assert len({lead.source_url for b in batches for lead in b.leads}) == 10
    assert adapter.search(REQUEST).status == "budget_exhausted"
    assert len(calls) == 3
    assert len(adapter_for(lambda r: response([member()])).search(REQUEST).leads) == 1


def test_response_cookies_never_sent_to_next_query_and_no_redirects():
    calls = []
    def handler(request):
        calls.append(request)
        assert "cookie" not in request.headers
        return httpx.Response(200, json={"web": {"results": []}},
                              headers={"Set-Cookie": "ambient=private; Path=/"})
    adapter = adapter_for(handler)
    assert adapter.search(REQUEST).status == "not_found"
    assert adapter.search(REQUEST).status == "not_found"
    assert len(calls) == 2


def test_injected_transport_stays_caller_owned_across_cookie_isolated_queries(monkeypatch):
    closed = []
    calls = []

    def handler(request):
        if closed:
            raise httpx.ConnectError("caller transport closed")
        calls.append(request)
        assert request.headers.get("Cookie") is None
        return httpx.Response(200, json={"web": {"results": [member(
            f"https://employer.example/jobs/{len(calls)}",
        )]}}, headers={"Set-Cookie": "ambient=private; Path=/"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(transport, "close", lambda: closed.append(True))
    adapter = BravePublicSearchAdapter(
        PublicSearchConfig(enabled=True, api_key="fake-test-key"), transport=transport,
    )
    assert [adapter.search(REQUEST).status for _ in range(2)] == ["found", "found"]
    assert len(calls) == 2 and not closed
    transport.close()
    assert closed == [True]


def test_client_security_configuration(monkeypatch):
    original = httpx.Client
    observed = []
    def client(**kwargs):
        observed.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(httpx, "Client", client)
    assert adapter_for(lambda r: response([])).search(REQUEST).status == "not_found"
    assert observed[0]["trust_env"] is False
    assert observed[0]["follow_redirects"] is False
    assert observed[0]["timeout"] == 5.0


@pytest.mark.parametrize("destination", [
    "https://user:synthetic-secret@public.example/jobs",
    "https://user@public.example/jobs",
    "https://public.example/jobs?access_token=synthetic-secret",
    "https://public.example/jobs?secretkey=synthetic-secret",
    "https://public.example/jobs?%2561ccess_token=synthetic-secret",
    "https://public.example/jobs#access_token=synthetic-secret",
])
@pytest.mark.parametrize("encoding", [0, 1, 2])
def test_candidate_rejects_nested_credentials(destination, encoding):
    for _ in range(encoding):
        destination = quote(destination, safe="")
    assert _candidate_url("https://employer.example/jobs?next=" + destination) is None


@pytest.mark.parametrize("fragment", [
    "access_token=synthetic-secret", "/callback?client_secret=synthetic-secret",
    "password=synthetic-secret", "%2561ccess_token=synthetic-secret",
    "next=https%3A%2F%2Fuser%3Asynthetic-secret%40public.example%2F",
    "next=https://public.example/?secret_key=synthetic-secret",
])
def test_candidate_checks_fragment_before_discarding(fragment):
    assert _candidate_url("https://employer.example/jobs#" + fragment) is None


@pytest.mark.parametrize("fragment", [
    "https%3A%2F%2Fuser%3Asynthetic-secret%40public.example%2F?view=jobs",
    "/callback%3Faccess_token=synthetic-secret",
    "%2Fcallback%3Faccess_token=synthetic-secret",
])
def test_candidate_rejects_encoded_fragment_uri_with_literal_assignment(fragment):
    assert _candidate_url("https://employer.example/jobs#" + fragment) is None


@pytest.mark.parametrize("suffix", [
    "?%2561ccess_token=synthetic-secret",
    "?next=https%252525253A%252525252F%252525252Fpublic.example",
    "?next=https://public.example/%ZZ?token=synthetic-secret",
])
def test_candidate_rejects_ambiguous_encoded_credentials(suffix):
    assert _candidate_url("https://employer.example/jobs" + suffix) is None


def test_candidate_bounds_nested_inspection():
    destination = "https://public.example/jobs"
    for _ in range(8):
        destination = "https://public.example/?next=" + quote(destination, safe="")
    assert _candidate_url(destination) is None


@pytest.mark.parametrize("fragment", [
    "password", "token", "/jobs/password", "view%26token=jobs",
    "https%3A%2F%2Fpublic.example%2FC++?view=jobs",
    "/callback%3Fview=jobs", "%2Fcallback%3Fview=jobs",
    "description=password%3Drotation",
])
def test_candidate_allows_noncredential_fragment_text(fragment):
    assert _candidate_url("https://employer.example/jobs#" + fragment) == (
        "https://employer.example/jobs"
    )


@pytest.mark.parametrize("destination", [
    "https://public.example/%ZZ", "https://public.example/%",
    "https://public.example/?next=https%252525253A%252525252F",
])
def test_candidate_rejects_incomplete_nested_url_encoding(destination):
    assert _candidate_url("https://employer.example/?continue=" + destination) is None


@pytest.mark.parametrize("destination", [
    "https://Public.Example:443/jobs?id=123#details",
    "https://public.example/jobs?q=password&description=password%3Drotation",
    "//public.example/jobs#/openings?department=engineering",
])
@pytest.mark.parametrize("encoding", [0, 1, 2])
def test_candidate_preserves_safe_nested_destinations(destination, encoding):
    for _ in range(encoding):
        destination = quote(destination, safe="")
    value = "https://EMPLOYER.example:443/jobs?redirect=" + destination
    assert _candidate_url(value) == value.replace("EMPLOYER.example:443", "employer.example").split("#")[0]


@pytest.mark.parametrize("value", [
    "http://employer.example/jobs", "https://employer.example:444/",
    "https://127.0.0.1/", "https://employer.internal/", "https://employer.example/a%20b",
    "https://employer.example/a%5Cb", "https://employer.example/" + "a" * 2048,
])
def test_candidate_preserves_public_url_restrictions(value):
    assert _candidate_url(value) is None


def test_candidate_accepts_maximum_length_public_url_and_depth_control():
    value = "https://EMPLOYER.example/".ljust(2048, "a")
    assert _candidate_url(value) == value.replace("EMPLOYER", "employer")
    destination = "https://public.example/jobs"
    for _ in range(4):
        destination = "https://public.example/?next=" + quote(destination, safe="")
    assert _candidate_url(destination) == destination


def test_contract_url_filter():
    assert _candidate_url("https://employer.example/jobs/1#fragment") == "https://employer.example/jobs/1"
    assert _candidate_url("https://127.0.0.1/") is None
    assert _candidate_url("https://employer.example/?token=synthetic") is None
