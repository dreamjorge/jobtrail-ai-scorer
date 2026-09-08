"""Hermetic HTTP and retry contract tests for the Lever source adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import httpx
import pytest

from jobtrail_ai_scorer.retry import RetryPolicy
from jobtrail_ai_scorer.sources import SourceSearchRequest
from jobtrail_ai_scorer.sources.lever import (
    LeverHttpError,
    LeverSourceAdapter,
    LeverTransientError,
)


BASE_URL = "https://lever.test/v0"


def _policy(max_attempts: int = 3) -> RetryPolicy:
    return RetryPolicy(max_attempts=max_attempts, base_delay=0.0, max_delay=0.0)


def _posting(posting_id: str, title: str = "Engineer") -> dict[str, Any]:
    return {"id": posting_id, "text": title, "categories": {"location": "Remote"}}


def _request(*, results_wanted: int = 10, profile_name: str | None = "backend") -> SourceSearchRequest:
    return SourceSearchRequest(
        sites=("lever",),
        search_term="ignored",
        location="ignored",
        results_wanted=results_wanted,
        hours_old=24,
        is_remote=False,
        profile_name=profile_name,
    )


def _adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    boards: tuple[str, ...] = ("acme",),
    retry_policy: RetryPolicy | None = None,
    captured: list[httpx.Request] | None = None,
) -> tuple[LeverSourceAdapter, httpx.Client]:
    requests = captured if captured is not None else []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return (
        LeverSourceAdapter(
            boards,
            client=client,
            base_url=BASE_URL,
            retry_policy=retry_policy or _policy(),
            retry_sleep=lambda _delay: None,
            clock=lambda: datetime(2026, 9, 8, 10, tzinfo=timezone.utc),
        ),
        client,
    )


def test_search_makes_one_get_with_mode_query_and_applies_page_cap() -> None:
    captured: list[httpx.Request] = []
    adapter, client = _adapter(
        lambda request: httpx.Response(
            200,
            json=[_posting("one"), _posting("two"), _posting("three")],
            request=request,
        ),
        boards=("acme", "globex"),
        captured=captured,
    )
    try:
        jobs = adapter.search(_request(results_wanted=2))
    finally:
        client.close()

    assert [job.source_job_id for job in jobs] == ["one", "two"]
    assert len(captured) == 1
    assert captured[0].method == "GET"
    assert captured[0].url.path == "/v0/postings/acme"
    assert dict(captured[0].url.params) == {"mode": "json"}


def test_search_propagates_profile_name_to_normalized_jobs() -> None:
    adapter, client = _adapter(
        lambda request: httpx.Response(200, json=[_posting("one")], request=request)
    )
    try:
        jobs = adapter.search(_request(profile_name="platform"))
    finally:
        client.close()

    assert len(jobs) == 1
    assert jobs[0].search_profile == "platform"


def test_4xx_is_terminal_and_error_does_not_leak_secret_or_url() -> None:
    secret = "lever-api-secret"
    url = f"{BASE_URL}/postings/{secret}"
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(401, text="token=" + secret, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = LeverSourceAdapter(
        (secret,),
        client=client,
        base_url=BASE_URL,
        retry_policy=_policy(3),
        retry_sleep=lambda _delay: None,
    )
    try:
        with pytest.raises(LeverHttpError) as caught:
            adapter.search(_request())
    finally:
        client.close()

    message = str(caught.value)
    assert caught.value.status_code == 401
    assert secret not in message
    assert url not in message
    assert len(captured) == 1


def test_search_visits_second_board_when_first_is_under_cap() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        board = request.url.path.rsplit("/", 1)[-1]
        rows = [_posting("acme-job")] if board == "acme" else [_posting("globex-job")]
        return httpx.Response(200, json=rows, request=request)

    adapter, client = _adapter(
        handler,
        boards=("acme", "globex"),
        captured=captured,
    )
    try:
        jobs = adapter.search(_request(results_wanted=2))
    finally:
        client.close()

    assert [job.source_job_id for job in jobs] == ["acme-job", "globex-job"]
    assert [request.url.path for request in captured] == [
        "/v0/postings/acme",
        "/v0/postings/globex",
    ]


def test_5xx_retries_and_recovers() -> None:
    statuses = iter((503, 200))
    captured: list[httpx.Request] = []
    adapter, client = _adapter(
        lambda request: httpx.Response(next(statuses), json=[_posting("ok")], request=request),
        retry_policy=_policy(2),
        captured=captured,
    )
    try:
        jobs = adapter.search(_request())
    finally:
        client.close()

    assert [job.source_job_id for job in jobs] == ["ok"]
    assert len(captured) == 2


def test_5xx_exhaustion_raises_transient_error_with_bounded_attempts() -> None:
    captured: list[httpx.Request] = []
    adapter, client = _adapter(
        lambda request: httpx.Response(502, request=request),
        retry_policy=_policy(2),
        captured=captured,
    )
    try:
        with pytest.raises(LeverTransientError) as caught:
            adapter.search(_request())
    finally:
        client.close()

    assert caught.value.attempts == 2
    assert len(captured) == 2
    assert "https://lever.test" not in str(caught.value)


def test_transport_error_retries_and_recovers() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary network failure", request=request)
        return httpx.Response(200, json=[_posting("recovered")], request=request)

    adapter, client = _adapter(handler, retry_policy=_policy(2))
    try:
        jobs = adapter.search(_request())
    finally:
        client.close()

    assert [job.source_job_id for job in jobs] == ["recovered"]
    assert calls == 2


@pytest.mark.parametrize(
    ("content", "json_payload", "expected"),
    [
        (b"{not-json", None, LeverHttpError),
        (b"present", {"postings": []}, list),
    ],
)
def test_malformed_or_non_list_payload_behavior(
    content: bytes, json_payload: Any, expected: type[BaseException] | type[list]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if json_payload is None:
            return httpx.Response(200, content=content, request=request)
        return httpx.Response(200, json=json_payload, request=request)

    adapter, client = _adapter(handler)
    try:
        if expected is LeverHttpError:
            with pytest.raises(LeverHttpError, match="invalid JSON"):
                adapter.search(_request())
        else:
            assert adapter.search(_request()) == []
    finally:
        client.close()


def test_close_only_closes_client_owned_by_adapter() -> None:
    injected = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)))
    injected_adapter = LeverSourceAdapter(("acme",), client=injected, base_url=BASE_URL)
    injected_adapter.close()
    assert not injected.is_closed
    injected.close()
    injected.close()

    owned = LeverSourceAdapter(("acme",), base_url=BASE_URL)
    owned_client = owned._client
    assert not owned_client.is_closed
    owned.close()
    assert owned_client.is_closed
