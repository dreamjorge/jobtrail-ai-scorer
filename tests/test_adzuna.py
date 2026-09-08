"""Tests for the Adzuna source adapter.

The adapter exposes:
* a normalization helper that converts a raw Adzuna result record into a
  ``NormalizedJob``;
* an :class:`AdzunaConfig` dataclass that loads credentials from the
  environment with explicit disable semantics;
* an :class:`AdzunaSourceAdapter` that maps a ``SourceSearchRequest`` to a
  single-page Adzuna GET request and normalizes each ``results`` entry.

The HTTP transport is injected through ``httpx.Client`` so the suite stays
hermetic: tests pass an ``httpx.MockTransport`` while production callers
provide a real ``httpx.Client``. The adapter reuses the project's existing
``RetryPolicy`` so 5xx and transport failures retry with bounded attempts,
while 4xx responses surface immediately as ``AdzunaHttpError`` and exhausted
retries surface as ``AdzunaTransientError``.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

import httpx
import pytest

from jobtrail_ai_scorer.retry import RetryPolicy
from jobtrail_ai_scorer.sources import NormalizedJob, SourceSearchRequest
from jobtrail_ai_scorer.sources.adzuna import (
    ADZUNA_LOGGER_NAME,
    DEFAULT_BASE_URL,
    MAX_PER_PAGE,
    SOURCE_NAME,
    AdzunaConfig,
    AdzunaHttpError,
    AdzunaSourceAdapter,
    AdzunaTransientError,
    country_to_currency,
    normalize_adzuna_job,
)


# --- helpers ---------------------------------------------------------------


def _fast_retry_policy(*, max_attempts: int = 3) -> RetryPolicy:
    """Return a ``RetryPolicy`` with negligible backoff so tests stay fast."""

    return RetryPolicy(max_attempts=max_attempts, base_delay=0.01, max_delay=0.01)


def _make_adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    country: str = "us",
    app_id: str = "id-123",
    app_key: str = "key-456",
    base_url: str = DEFAULT_BASE_URL,
    retry_policy: RetryPolicy | None = None,
    captured: list[httpx.Request] | None = None,
) -> tuple[AdzunaSourceAdapter, httpx.Client]:
    """Build an ``AdzunaSourceAdapter`` driven by an ``httpx.MockTransport``.

    The handler can be a simple callable returning a single response, or it
    can be a stateful function that yields different responses per call. The
    optional ``captured`` list receives every ``httpx.Request`` the adapter
    sends so tests can assert the URL path, query parameters, and method.
    """

    captured = captured if captured is not None else []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(_wrapped))
    config = AdzunaConfig(
        app_id=app_id,
        app_key=app_key,
        country=country,
        base_url=base_url,
    )
    adapter = AdzunaSourceAdapter(
        config=config,
        client=client,
        retry_policy=retry_policy or _fast_retry_policy(),
    )
    return adapter, client


def _full_adzuna_record() -> dict[str, Any]:
    return {
        "id": "12345",
        "title": "Senior Python Developer",
        "company": {"display_name": "Acme Corp"},
        "description": "<p>Build amazing things.</p>",
        "redirect_url": "https://www.adzuna.com.mx/jobs/land/12345",
        "location": {"display_name": "Mexico City, Mexico"},
        "salary_min": 50000.0,
        "salary_max": 80000.0,
        "contract_type": "permanent",
        "contract_time": "full_time",
        "created": "2026-09-08T10:00:00Z",
    }


def _adzuna_record(
    salary_min: float | None = None, salary_max: float | None = None
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": "1",
        "title": "Engineer",
        "company": {"display_name": "Co"},
        "location": {"display_name": "Remote"},
        "redirect_url": "https://www.adzuna.com/jobs/land/1",
    }
    if salary_min is not None:
        record["salary_min"] = salary_min
    if salary_max is not None:
        record["salary_max"] = salary_max
    return record


def _json_response(payload: Any, request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=payload, request=request)


# ---------------------------------------------------------------------
# Task 1 (migrated): normalization + happy-path request translation
# ---------------------------------------------------------------------


def test_adzuna_adapter_happy_path_normalizes_full_record():
    payload = {"results": [_full_adzuna_record()]}
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(payload, request)

    adapter, _client = _make_adapter(handler, country="mx", captured=captured)
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Mexico City",
        results_wanted=10,
        hours_old=72,
        is_remote=False,
        profile_name="backend",
    )

    jobs = adapter.search(request)

    assert jobs == [
        NormalizedJob(
            source="adzuna",
            source_job_id="12345",
            title="Senior Python Developer",
            company="Acme Corp",
            description="<p>Build amazing things.</p>",
            source_url="https://www.adzuna.com.mx/jobs/land/12345",
            location="Mexico City, Mexico",
            salary_min=50000.0,
            salary_max=80000.0,
            salary_currency="MXN",
            job_type="permanent/full_time",
            search_profile="backend",
            retrieved_at="2026-09-08T10:00:00Z",
        )
    ]
    # Verify the request translation: GET, the documented URL path with the
    # country interpolated, and the documented query parameters.
    assert len(captured) == 1
    sent = captured[0]
    assert sent.method == "GET"
    assert str(sent.url).startswith("https://api.adzuna.com/v1/jobs/mx/search/1")
    params = dict(sent.url.params)
    assert params["what"] == "python"
    assert params["where"] == "Mexico City"
    assert params["results_per_page"] == "10"
    assert params["max_days_old"] == "72"
    # The adapter must also include the credentials on the request so the
    # Adzuna API can authenticate the call.
    assert params["app_id"] == "id-123"
    assert params["app_key"] == "key-456"


def test_adzuna_adapter_omits_missing_optional_fields():
    payload = {
        "results": [
            {
                "id": "99999",
                "title": "Contract Role",
                "company": {"display_name": "Solo Inc"},
                "location": {"display_name": "Remote"},
                "redirect_url": "https://www.adzuna.com/jobs/land/99999",
                # description, salary_min/max, contract_*, created intentionally omitted
            }
        ]
    }
    adapter, _client = _make_adapter(
        lambda request: _json_response(payload, request), country="us"
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="contract",
        location="Remote",
        results_wanted=1,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    jobs = adapter.search(request)

    assert jobs == [
        NormalizedJob(
            source="adzuna",
            source_job_id="99999",
            title="Contract Role",
            company="Solo Inc",
            source_url="https://www.adzuna.com/jobs/land/99999",
            location="Remote",
            salary_currency="USD",
            search_profile="default",
        )
    ]
    # None fields must be absent so ``to_import_payload`` drops them.
    job = jobs[0]
    assert job.description is None
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.job_type is None
    assert job.retrieved_at is None
    assert job.to_import_payload() == {
        "source": "adzuna",
        "sourceJobId": "99999",
        "company": "Solo Inc",
        "position": "Contract Role",
        "jobUrl": "https://www.adzuna.com/jobs/land/99999",
        "location": "Remote",
        "salaryCurrency": "USD",
        "searchProfile": "default",
    }


def test_adzuna_adapter_propagates_request_profile_name_to_each_job():
    payload = {
        "results": [
            {
                "id": "1",
                "title": "Backend Engineer",
                "company": {"display_name": "Co"},
                "redirect_url": "https://www.adzuna.com/jobs/land/1",
                "location": {"display_name": "Remote"},
            },
            {
                "id": "2",
                "title": "Backend Engineer II",
                "company": {"display_name": "Co"},
                "redirect_url": "https://www.adzuna.com/jobs/land/2",
                "location": {"display_name": "Remote"},
            },
        ]
    }
    adapter, _client = _make_adapter(
        lambda request: _json_response(payload, request), country="us"
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="backend",
        location="Remote",
        results_wanted=2,
        hours_old=24,
        is_remote=True,
        profile_name="data-engineering",
    )

    jobs = adapter.search(request)

    assert len(jobs) == 2
    assert all(job.search_profile == "data-engineering" for job in jobs)
    assert [job.source_job_id for job in jobs] == ["1", "2"]


def test_normalize_adzuna_job_handles_partial_contract_fields():
    """Either ``contract_type`` or ``contract_time`` may be present alone."""

    permanent_only = {
        "id": "a",
        "title": "Permanent Role",
        "company": {"display_name": "Co"},
        "redirect_url": "https://www.adzuna.com/jobs/land/a",
        "contract_type": "permanent",
    }
    time_only = {
        "id": "b",
        "title": "Time Only Role",
        "company": {"display_name": "Co"},
        "redirect_url": "https://www.adzuna.com/jobs/land/b",
        "contract_time": "part_time",
    }

    perm_job = normalize_adzuna_job(permanent_only, profile_name="default")
    time_job = normalize_adzuna_job(time_only, profile_name="default")

    assert perm_job.job_type == "permanent"
    assert time_job.job_type == "part_time"


def test_adzuna_adapter_returns_empty_list_when_results_missing():
    """A payload without a ``results`` key must not crash the call."""

    adapter, _client = _make_adapter(
        lambda request: _json_response({}, request), country="mx"
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    assert adapter.search(request) == []


# ---------------------------------------------------------------------
# Task 1: results_per_page capping + query translation contract
# ---------------------------------------------------------------------


def test_adzuna_adapter_caps_results_per_page_at_max_per_page():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"results": []}, request)

    adapter, _client = _make_adapter(handler, captured=captured)
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=MAX_PER_PAGE * 4,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    adapter.search(request)

    params = dict(captured[0].url.params)
    assert params["results_per_page"] == str(MAX_PER_PAGE)


def test_adzuna_adapter_interpolates_country_into_url_path():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"results": []}, request)

    adapter, _client = _make_adapter(handler, country="mx", captured=captured)
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Mexico City",
        results_wanted=10,
        hours_old=72,
        is_remote=False,
        profile_name="default",
    )

    adapter.search(request)

    # Country must be interpolated into the documented URL path; the
    # ``/v1`` prefix comes from ``config.base_url``.
    path = captured[0].url.path
    assert path == "/v1/jobs/mx/search/1"


def test_adzuna_adapter_sends_only_get_requests():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"results": []}, request)

    adapter, _client = _make_adapter(handler, captured=captured)
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    adapter.search(request)

    # ``SourceAdapter.search`` is required to perform GETs only.
    assert all(req.method == "GET" for req in captured)


# ---------------------------------------------------------------------
# Task 3 (new): HTTP error semantics — 4xx terminal, 5xx retry, exhaustion
# ---------------------------------------------------------------------


def test_adzuna_adapter_4xx_response_raises_adzuna_http_error_terminal():
    """4xx responses are terminal: the adapter raises ``AdzunaHttpError``
    immediately and does not retry."""

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    adapter, _client = _make_adapter(
        handler,
        retry_policy=_fast_retry_policy(max_attempts=5),
        captured=captured,
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    with pytest.raises(AdzunaHttpError) as exc_info:
        adapter.search(request)

    assert exc_info.value.status_code == 404
    # Terminal classification: a single attempt only, no sleeps.
    assert len(captured) == 1


def test_adzuna_adapter_4xx_error_never_includes_request_url_with_credentials():
    """The error message must not leak the URL (which carries credentials)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    adapter, _client = _make_adapter(handler)
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    with pytest.raises(AdzunaHttpError) as exc_info:
        adapter.search(request)

    message = str(exc_info.value)
    assert "id-123" not in message
    assert "key-456" not in message
    # The credentialed query parameter names must not appear in the message
    # either, because they would imply the URL is part of the error.
    assert "app_key=" not in message


def test_adzuna_adapter_5xx_response_retries_and_recovers_on_success():
    """5xx responses are retryable; an eventual success returns the jobs."""

    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            return httpx.Response(503, request=request)
        return _json_response({"results": [_adzuna_record()]}, request)

    adapter, _client = _make_adapter(
        handler,
        retry_policy=_fast_retry_policy(max_attempts=4),
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    jobs = adapter.search(request)

    assert len(jobs) == 1
    assert jobs[0].source_job_id == "1"
    assert len(attempts) == 3


def test_adzuna_adapter_exhausted_5xx_retries_raise_adzuna_transient_error():
    """When retries are exhausted the adapter raises ``AdzunaTransientError``."""

    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(len(attempts) + 1)
        return httpx.Response(503, request=request)

    adapter, _client = _make_adapter(
        handler,
        retry_policy=_fast_retry_policy(max_attempts=3),
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    with pytest.raises(AdzunaTransientError) as exc_info:
        adapter.search(request)

    assert exc_info.value.attempts == 3
    # The underlying cause is the last 5xx ``HTTPStatusError`` so operators
    # can introspect the original failure without leaking the URL.
    assert isinstance(exc_info.value.__cause__, httpx.HTTPStatusError)
    assert exc_info.value.__cause__.response.status_code == 503
    assert len(attempts) == 3


def test_adzuna_adapter_transient_error_message_does_not_leak_credentials():
    """The transient-error message must not contain credential values."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, request=request)

    adapter, _client = _make_adapter(
        handler,
        app_id="ULTRA-SECRET-ID",
        app_key="ULTRA-SECRET-KEY",
        retry_policy=_fast_retry_policy(max_attempts=2),
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    with pytest.raises(AdzunaTransientError) as exc_info:
        adapter.search(request)

    message = str(exc_info.value)
    assert "ULTRA-SECRET-ID" not in message
    assert "ULTRA-SECRET-KEY" not in message
    assert "app_key=" not in message


def test_adzuna_adapter_retries_on_transport_errors_and_recovers():
    """``httpx.ConnectError`` is transient; the adapter retries and recovers."""

    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 2:
            raise httpx.ConnectError("connection refused", request=request)
        return _json_response({"results": [_adzuna_record()]}, request)

    adapter, _client = _make_adapter(
        handler,
        retry_policy=_fast_retry_policy(max_attempts=3),
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    jobs = adapter.search(request)

    assert len(jobs) == 1
    assert len(attempts) == 2


def test_adzuna_adapter_transport_retry_exhaustion_raises_transient_error():
    """A transport error that survives retries also raises
    ``AdzunaTransientError``."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    adapter, _client = _make_adapter(
        handler,
        retry_policy=_fast_retry_policy(max_attempts=2),
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    with pytest.raises(AdzunaTransientError) as exc_info:
        adapter.search(request)

    assert exc_info.value.attempts == 2
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


def test_adzuna_adapter_skips_malformed_items_without_failing_call():
    """Non-mapping entries inside ``results`` must be silently skipped."""

    payload = {
        "results": [
            _adzuna_record(),
            "this is not a mapping",
            42,
            None,
            {
                "id": "2",
                "title": "Second",
                "company": {"display_name": "Co2"},
                "location": {"display_name": "Remote"},
                "redirect_url": "https://www.adzuna.com/jobs/land/2",
            },
            ["also", "not", "a", "mapping"],
        ]
    }
    adapter, _client = _make_adapter(
        lambda request: _json_response(payload, request), country="us"
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=10,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    jobs = adapter.search(request)

    # Only the two Mapping entries survive normalization.
    assert [job.source_job_id for job in jobs] == ["1", "2"]


def test_adzuna_adapter_skips_results_list_when_payload_malformed():
    """A non-list ``results`` value must not fail the call."""

    payload = {"results": "not a list"}
    adapter, _client = _make_adapter(
        lambda request: _json_response(payload, request), country="us"
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    assert adapter.search(request) == []


def test_adzuna_adapter_skips_results_when_payload_is_not_a_mapping():
    """A bare-list or scalar payload must not crash the call."""

    for value in ([{"id": "1"}], "scalar", 42):
        adapter, _client = _make_adapter(
            lambda request, _value=value: _json_response(_value, request),
            country="us",
        )
        request = SourceSearchRequest(
            sites=("adzuna",),
            search_term="python",
            location="Remote",
            results_wanted=5,
            hours_old=24,
            is_remote=True,
            profile_name="default",
        )
        assert adapter.search(request) == []


# ---------------------------------------------------------------------
# Task 3 (new): retry metadata propagation & HTTP-status classification
# ---------------------------------------------------------------------


def test_adzuna_http_error_status_code_makes_classifier_terminal():
    """``AdzunaHttpError`` carries ``status_code`` so the retry classifier
    recognizes it as terminal without depending on this module."""

    from jobtrail_ai_scorer.retry import TERMINAL, classify_retryable

    # 4xx responses are terminal: the adapter never retries them.
    err_4xx = AdzunaHttpError("rejected", status_code=400)
    assert classify_retryable(err_4xx) == TERMINAL


def test_adzuna_transient_error_is_classified_as_terminal_by_default():
    """``AdzunaTransientError`` does not carry ``status_code`` so the retry
    helper treats it as a configuration-level terminal failure (no further
    retry from the orchestrator)."""

    from jobtrail_ai_scorer.retry import TERMINAL, classify_retryable

    err = AdzunaTransientError("exhausted", attempts=3)
    assert classify_retryable(err) == TERMINAL


# ---------------------------------------------------------------------
# Task 2 (migrated): AdzunaConfig + disable semantics
# ---------------------------------------------------------------------


def test_adzuna_config_from_env_populates_defaults_when_credentials_present():
    config = AdzunaConfig.from_env(
        {"ADZUNA_APP_ID": "id-123", "ADZUNA_APP_KEY": "key-456"}
    )

    assert config is not None
    assert config.app_id == "id-123"
    assert config.app_key == "key-456"
    assert config.country == "us"
    assert config.base_url == "https://api.adzuna.com/v1"
    assert config.enabled is True


def test_adzuna_config_from_env_returns_none_when_app_id_missing(caplog):
    with caplog.at_level("WARNING", logger=ADZUNA_LOGGER_NAME):
        config = AdzunaConfig.from_env({})

    assert config is None
    # Silent disable when both credentials are absent.
    assert caplog.records == []


def test_adzuna_config_from_env_returns_none_when_app_key_missing(caplog):
    with caplog.at_level("WARNING", logger=ADZUNA_LOGGER_NAME):
        # Both credentials absent => silent disable, regardless of which key
        # the operator thinks of as "missing". Listed separately so the
        # contract is documented for either perspective.
        config = AdzunaConfig.from_env({"SOME_OTHER_VAR": "noise"})

    assert config is None
    # Silent disable when both credentials are absent.
    assert caplog.records == []


def test_adzuna_config_from_env_warns_when_only_app_id_is_present(caplog):
    with caplog.at_level("WARNING", logger=ADZUNA_LOGGER_NAME):
        config = AdzunaConfig.from_env({"ADZUNA_APP_ID": "secret-app-id"})

    assert config is None
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "ValueError" in message
    # The secret value must never appear in the log message.
    assert "secret-app-id" not in message


def test_adzuna_config_from_env_warns_when_only_app_key_is_present(caplog):
    with caplog.at_level("WARNING", logger=ADZUNA_LOGGER_NAME):
        config = AdzunaConfig.from_env({"ADZUNA_APP_KEY": "secret-app-key"})

    assert config is None
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "ValueError" in message
    assert "secret-app-key" not in message


def test_adzuna_config_from_env_disable_flag_overrides_valid_credentials():
    env = {
        "ADZUNA_APP_ID": "id-123",
        "ADZUNA_APP_KEY": "key-456",
        "JOB_DISABLE_ADZUNA": "1",
    }
    assert AdzunaConfig.from_env(env) is None


def test_adzuna_config_from_env_accepts_truthy_disable_variants():
    env = {"ADZUNA_APP_ID": "id-123", "ADZUNA_APP_KEY": "key-456"}
    for truthy in ("1", "true", "yes", "on"):
        assert AdzunaConfig.from_env({**env, "JOB_DISABLE_ADZUNA": truthy}) is None


def test_adzuna_config_from_env_strips_whitespace_around_credentials():
    config = AdzunaConfig.from_env(
        {"ADZUNA_APP_ID": "  id-123  ", "ADZUNA_APP_KEY": " key-456 "}
    )

    assert config is not None
    assert config.app_id == "id-123"
    assert config.app_key == "key-456"


def test_adzuna_config_from_env_honors_country_and_base_url_overrides():
    config = AdzunaConfig.from_env(
        {
            "ADZUNA_APP_ID": "id-123",
            "ADZUNA_APP_KEY": "key-456",
            "ADZUNA_COUNTRY": "gb",
            "ADZUNA_BASE_URL": "https://mirror.example.com/v2",
        }
    )

    assert config is not None
    assert config.country == "gb"
    assert config.base_url == "https://mirror.example.com/v2"


def test_adzuna_config_blank_disable_string_does_not_disable():
    config = AdzunaConfig.from_env(
        {
            "ADZUNA_APP_ID": "id-123",
            "ADZUNA_APP_KEY": "key-456",
            "JOB_DISABLE_ADZUNA": "0",
        }
    )
    assert config is not None


# --- salary_currency wiring ---------------------------------------------


def test_country_to_currency_maps_known_adzuna_country_codes():
    assert country_to_currency("us") == "USD"
    assert country_to_currency("gb") == "GBP"
    assert country_to_currency("mx") == "MXN"
    assert country_to_currency("de") == "EUR"
    assert country_to_currency("au") == "AUD"


def test_country_to_currency_returns_none_for_unknown_country():
    assert country_to_currency("xx") is None
    assert country_to_currency("") is None


def test_adzuna_adapter_wires_salary_currency_from_country_for_us():
    payload = {"results": [_adzuna_record(salary_min=100.0, salary_max=200.0)]}
    adapter, _client = _make_adapter(
        lambda request: _json_response(payload, request), country="us"
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=1,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    jobs = adapter.search(request)

    assert jobs[0].salary_currency == "USD"


def test_adzuna_adapter_wires_salary_currency_from_country_for_mx():
    payload = {"results": [_adzuna_record(salary_min=50000.0, salary_max=80000.0)]}
    adapter, _client = _make_adapter(
        lambda request: _json_response(payload, request), country="mx"
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Mexico City",
        results_wanted=1,
        hours_old=72,
        is_remote=False,
        profile_name="backend",
    )

    jobs = adapter.search(request)

    assert jobs[0].salary_currency == "MXN"


def test_adzuna_adapter_leaves_salary_currency_none_for_unknown_country():
    payload = {"results": [_adzuna_record()]}
    adapter, _client = _make_adapter(
        lambda request: _json_response(payload, request), country="xx"
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=1,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    jobs = adapter.search(request)

    assert jobs[0].salary_currency is None
    # Unknown currency must not appear in the import payload.
    assert "salaryCurrency" not in jobs[0].to_import_payload()


def test_adzuna_adapter_includes_salary_currency_in_import_payload():
    """Known-country currency must be propagated into the import payload."""

    payload = {"results": [_adzuna_record(salary_min=100.0, salary_max=200.0)]}
    adapter, _client = _make_adapter(
        lambda request: _json_response(payload, request), country="gb"
    )
    request = SourceSearchRequest(
        sites=("adzuna",),
        search_term="python",
        location="Remote",
        results_wanted=1,
        hours_old=24,
        is_remote=True,
        profile_name="default",
    )

    jobs = adapter.search(request)

    assert jobs[0].to_import_payload()["salaryCurrency"] == "GBP"


def test_adzuna_config_from_env_partial_credentials_uses_warning_level(caplog):
    """The warning must be emitted at WARNING level, never ERROR."""

    with caplog.at_level("WARNING", logger=ADZUNA_LOGGER_NAME):
        config = AdzunaConfig.from_env({"ADZUNA_APP_ID": "x"})

    assert config is None
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "WARNING"


def test_adzuna_config_from_env_partial_credentials_does_not_leak_either_secret(caplog):
    """The warning message must not contain any fragment of either secret value."""

    secret_id = "very-secret-app-id-XYZ"
    secret_key = "very-secret-app-key-ABC"
    with caplog.at_level("WARNING", logger=ADZUNA_LOGGER_NAME):
        # Provide only one credential at a time and check the message in both directions.
        config_id_only = AdzunaConfig.from_env({"ADZUNA_APP_ID": secret_id})
    assert config_id_only is None
    assert len(caplog.records) == 1
    assert secret_id not in caplog.records[0].getMessage()
    assert secret_key not in caplog.records[0].getMessage()

    caplog.clear()
    with caplog.at_level("WARNING", logger=ADZUNA_LOGGER_NAME):
        config_key_only = AdzunaConfig.from_env({"ADZUNA_APP_KEY": secret_key})
    assert config_key_only is None
    assert len(caplog.records) == 1
    assert secret_id not in caplog.records[0].getMessage()
    assert secret_key not in caplog.records[0].getMessage()


def test_adzuna_config_from_env_normalizes_country_to_lowercase():
    config = AdzunaConfig.from_env(
        {
            "ADZUNA_APP_ID": "id-123",
            "ADZUNA_APP_KEY": "key-456",
            "ADZUNA_COUNTRY": "GB",
        }
    )

    assert config is not None
    assert config.country == "gb"


def test_country_to_currency_is_case_insensitive():
    assert country_to_currency("US") == "USD"
    assert country_to_currency("Gb") == "GBP"
    assert country_to_currency("  mx  ") == "MXN"


# ---------------------------------------------------------------------
# Task 3 (new): close() ownership contract
# ---------------------------------------------------------------------


def test_adzuna_adapter_close_does_not_close_an_injected_client():
    """The adapter must not close an ``httpx.Client`` it did not create."""

    inner = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, request=req)))
    try:
        config = AdzunaConfig(app_id="id-123", app_key="key-456", country="us")
        adapter = AdzunaSourceAdapter(config=config, client=inner)

        adapter.close()

        assert not inner.is_closed
    finally:
        inner.close()


def test_adzuna_adapter_close_closes_an_owned_client():
    """When no client is injected, ``close()`` must release the owned client."""

    config = AdzunaConfig(app_id="id-123", app_key="key-456", country="us")
    adapter = AdzunaSourceAdapter(config=config)

    adapter.close()

    assert adapter._client.is_closed  # type: ignore[attr-defined]