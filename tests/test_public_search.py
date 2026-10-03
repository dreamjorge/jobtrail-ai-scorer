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


def test_contract_url_filter():
    assert _candidate_url("https://employer.example/jobs/1#fragment") == "https://employer.example/jobs/1"
    assert _candidate_url("https://127.0.0.1/") is None
    assert _candidate_url("https://employer.example/?token=synthetic") is None
