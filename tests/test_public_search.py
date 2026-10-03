from urllib.parse import quote

import pytest
from jobtrail_ai_scorer.notify import FORBIDDEN_TOKENS
from jobtrail_ai_scorer.public_search import PublicSearchConfig, PublicSearchRequest, _candidate_url

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
