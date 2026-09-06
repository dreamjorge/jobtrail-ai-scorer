"""Tests for the bounded retry helper.

These tests pin the public contract of :mod:`jobtrail_ai_scorer.retry`:
``RetryPolicy``, ``retry_call``, and ``classify_retryable``. The retry helper is
applied only to idempotent network calls, so the classifier must distinguish
5xx / network errors (retryable) from 4xx / configuration errors (terminal).
Backoff must be bounded by ``max_delay`` and ``jitter`` must be opt-in.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from jobtrail_ai_scorer.retry import (
    RETRY_LOG_PREFIX,
    RetryPolicy,
    classify_retryable,
    retry_call,
)


# --- retry_call: success and exhaustion ---------------------------------------


def test_retry_success_after_n_failures():
    """The helper returns the successful result after N transient failures."""

    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise httpx.ConnectError("connection refused")
        return "ok"

    sleeps: list[float] = []
    result = retry_call(
        flaky,
        policy=RetryPolicy(max_attempts=5, base_delay=0.01),
        sleep=lambda d: sleeps.append(d),
        label="unit",
    )

    assert result == "ok"
    assert attempts["count"] == 3
    # Two failures → exactly two sleep calls before the third (successful) attempt.
    assert len(sleeps) == 2


def test_retry_exhaustion_reraises_last_exception():
    """When the policy is exhausted, the helper re-raises the last exception."""

    attempts = {"count": 0}

    def always_fails():
        attempts["count"] += 1
        raise httpx.ConnectError(f"attempt-{attempts['count']}")

    sleeps: list[float] = []
    with pytest.raises(httpx.ConnectError) as exc_info:
        retry_call(
            always_fails,
            policy=RetryPolicy(max_attempts=3, base_delay=0.01),
            sleep=lambda d: sleeps.append(d),
        )

    # Last attempt is reflected in the error message.
    assert "attempt-3" in str(exc_info.value)
    assert attempts["count"] == 3
    # No sleep after the final attempt.
    assert len(sleeps) == 2


# --- retry_call: terminal classification -------------------------------------


def test_terminal_no_retry_on_4xx():
    """4xx HTTP status errors are terminal and raise immediately."""

    attempts = {"count": 0}
    request = httpx.Request("POST", "http://test.local/import")
    response = httpx.Response(404, request=request)

    def http_404():
        attempts["count"] += 1
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    sleeps: list[float] = []
    with pytest.raises(httpx.HTTPStatusError):
        retry_call(
            http_404,
            policy=RetryPolicy(max_attempts=5, base_delay=0.01),
            sleep=lambda d: sleeps.append(d),
        )

    assert attempts["count"] == 1
    assert sleeps == []  # no retries, no sleeps


def test_retry_on_5xx_then_success():
    """5xx HTTP status errors are retryable; eventual success returns normally."""

    request = httpx.Request("POST", "http://test.local/import")
    response = httpx.Response(503, request=request)

    attempts = {"count": 0}

    def http_503_then_ok():
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise httpx.HTTPStatusError(
                "service unavailable", request=request, response=response
            )
        return "ok"

    sleeps: list[float] = []
    result = retry_call(
        http_503_then_ok,
        policy=RetryPolicy(max_attempts=3, base_delay=0.01),
        sleep=lambda d: sleeps.append(d),
    )

    assert result == "ok"
    assert attempts["count"] == 2
    assert len(sleeps) == 1


def test_terminal_no_retry_on_value_error():
    """Generic ValueError is a terminal classification (configuration error)."""

    attempts = {"count": 0}

    def bad_input():
        attempts["count"] += 1
        raise ValueError("bad input")

    sleeps: list[float] = []
    with pytest.raises(ValueError):
        retry_call(
            bad_input,
            policy=RetryPolicy(max_attempts=5, base_delay=0.01),
            sleep=lambda d: sleeps.append(d),
        )

    assert attempts["count"] == 1
    assert sleeps == []


# --- backoff schedule and bounds ---------------------------------------------


def test_backoff_capped_by_max_delay():
    """Delays must be bounded by ``max_delay`` regardless of attempt index."""

    sleeps: list[float] = []

    def always_fails():
        raise httpx.ConnectError("boom")

    with pytest.raises(httpx.ConnectError):
        retry_call(
            always_fails,
            policy=RetryPolicy(max_attempts=10, base_delay=1.0, max_delay=4.0),
            sleep=lambda d: sleeps.append(d),
        )

    # Exponential would be 1, 2, 4, 8, 16, ... — capped at 4.0.
    assert sleeps[0] == pytest.approx(1.0)
    assert sleeps[1] == pytest.approx(2.0)
    assert sleeps[2] == pytest.approx(4.0)
    # Every recorded sleep stays within the cap.
    assert all(d <= 4.0 for d in sleeps)
    assert sleeps == sorted(sleeps)  # non-decreasing


def test_jitter_off_default_is_deterministic():
    """Without jitter, the delay schedule is deterministic across invocations."""

    sleeps_a: list[float] = []
    sleeps_b: list[float] = []

    def always_fails():
        raise httpx.ConnectError("boom")

    policy = RetryPolicy(max_attempts=4, base_delay=1.0, max_delay=8.0, jitter=False)
    with pytest.raises(httpx.ConnectError):
        retry_call(always_fails, policy=policy, sleep=lambda d: sleeps_a.append(d))
    with pytest.raises(httpx.ConnectError):
        retry_call(always_fails, policy=policy, sleep=lambda d: sleeps_b.append(d))

    assert sleeps_a == sleeps_b == [1.0, 2.0, 4.0]


def test_jitter_on_uses_bounded_random_delay():
    """With jitter, every delay is within ``[0, capped_delay]``."""

    sleeps: list[float] = []

    def always_fails():
        raise httpx.ConnectError("boom")

    policy = RetryPolicy(max_attempts=5, base_delay=2.0, max_delay=10.0, jitter=True)
    with pytest.raises(httpx.ConnectError):
        retry_call(always_fails, policy=policy, sleep=lambda d: sleeps.append(d))

    assert len(sleeps) == 4
    # Per-attempt caps: 2, 4, 8, 10 (clamped), 10 (clamped).
    expected_caps = [2.0, 4.0, 8.0, 10.0]
    for actual, cap in zip(sleeps, expected_caps):
        assert 0.0 <= actual <= cap


# --- partial success classification ------------------------------------------


def test_partial_success_classification_retryable_and_terminal():
    """Classifier labels 5xx / timeouts as retryable and 4xx / config errors as terminal."""

    req = httpx.Request("GET", "http://test.local/x")

    # Retryable
    assert (
        classify_retryable(
            httpx.HTTPStatusError(
                "503", request=req, response=httpx.Response(503, request=req)
            )
        )
        == "retryable"
    )
    assert (
        classify_retryable(
            httpx.HTTPStatusError(
                "502", request=req, response=httpx.Response(502, request=req)
            )
        )
        == "retryable"
    )
    assert classify_retryable(httpx.ConnectError("conn refused")) == "retryable"
    assert classify_retryable(httpx.ReadTimeout("read timeout")) == "retryable"
    assert classify_retryable(TimeoutError("timeout")) == "retryable"
    assert classify_retryable(ConnectionError("conn refused")) == "retryable"

    # Terminal
    assert (
        classify_retryable(
            httpx.HTTPStatusError(
                "404", request=req, response=httpx.Response(404, request=req)
            )
        )
        == "terminal"
    )
    assert (
        classify_retryable(
            httpx.HTTPStatusError(
                "401", request=req, response=httpx.Response(401, request=req)
            )
        )
        == "terminal"
    )
    assert (
        classify_retryable(
            httpx.HTTPStatusError(
                "400", request=req, response=httpx.Response(400, request=req)
            )
        )
        == "terminal"
    )
    assert classify_retryable(ValueError("bad input")) == "terminal"
    assert classify_retryable(KeyError("missing")) == "terminal"


def test_classify_retryable_handles_jobtrail_api_error_with_status_code():
    """``JobTrailApiError`` carrying a status code is classified by that code."""

    # Imported inside the test so a missing import does not break unrelated runs.
    from jobtrail_ai_scorer.jobtrail import JobTrailApiError

    # 4xx status is terminal.
    assert (
        classify_retryable(JobTrailApiError("bad", status_code=400))
        == "terminal"
    )
    # 5xx status is retryable.
    assert (
        classify_retryable(JobTrailApiError("boom", status_code=503))
        == "retryable"
    )
    # Unknown status code (network/transport) defaults to retryable.
    assert (
        classify_retryable(JobTrailApiError("connection refused", status_code=None))
        == "retryable"
    )


# --- logging and label propagation -------------------------------------------


def test_retry_call_logs_each_attempt_with_structured_prefix(caplog):
    """Each retry attempt emits a log record using the ``retry:`` prefix."""

    caplog.set_level(logging.WARNING, logger="jobtrail_ai_scorer.retry")

    request = httpx.Request("GET", "http://test.local/x")
    response = httpx.Response(503, request=request)

    def flaky():
        raise httpx.HTTPStatusError(
            "service unavailable", request=request, response=response
        )

    with pytest.raises(httpx.HTTPStatusError):
        retry_call(
            flaky,
            policy=RetryPolicy(max_attempts=3, base_delay=0.01),
            sleep=lambda d: None,
            label="unit-test",
        )

    messages = [record.getMessage() for record in caplog.records]
    assert messages, "expected at least one retry log record"
    assert any(message.startswith(RETRY_LOG_PREFIX) for message in messages)
    # The label propagates so operators can attribute retries to the call site.
    assert any("unit-test" in message for message in messages)
    # The classifier label is recorded too.
    assert any("retryable" in message or "exhausted" in message for message in messages)


# --- RetryPolicy validation --------------------------------------------------


def test_retry_policy_rejects_invalid_arguments():
    """Invalid policies raise ``ValueError`` at construction time."""

    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=-1.0)
    with pytest.raises(ValueError):
        RetryPolicy(max_delay=-1.0)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=10.0, max_delay=1.0)


# --- Triangulation: additional classification cases --------------------------


def test_classify_retryable_subprocess_error_is_retryable():
    """Subprocess failures from idempotent commands are retryable."""

    import subprocess

    # ``CalledProcessError`` with a non-zero return code is classified as
    # retryable so the scorer subprocess can survive transient provider blips.
    err = subprocess.CalledProcessError(returncode=1, cmd=["jobtrail-ai-scorer"])
    assert classify_retryable(err) == "retryable"


def test_classify_retryable_permission_error_is_terminal():
    """``PermissionError`` is a configuration failure and is terminal."""

    assert classify_retryable(PermissionError("denied")) == "terminal"


def test_classify_retryable_file_not_found_is_terminal():
    """``FileNotFoundError`` is a configuration failure and is terminal."""

    assert classify_retryable(FileNotFoundError("missing")) == "terminal"


def test_retry_call_passes_kwargs_through():
    """``retry_call`` forwards keyword arguments to the wrapped callable."""

    received: dict = {}

    def func(*args, **kwargs):
        received.update({"args": args, "kwargs": kwargs})
        return "ok"

    result = retry_call(func, "a", "b", key="value", policy=RetryPolicy(max_attempts=1))
    assert result == "ok"
    assert received == {"args": ("a", "b"), "kwargs": {"key": "value"}}


def test_retry_call_annotates_exception_with_metadata():
    """The raised exception carries ``retry_metadata`` for callers to introspect."""

    def always_fails():
        raise httpx.ConnectError("boom")

    with pytest.raises(httpx.ConnectError) as exc_info:
        retry_call(
            always_fails,
            policy=RetryPolicy(max_attempts=3, base_delay=0.01),
            sleep=lambda d: None,
        )

    metadata = getattr(exc_info.value, "retry_metadata", None)
    assert metadata == {"attempts": 3, "classification": "exhausted"}


def test_retry_call_default_policy_matches_three_attempts():
    """The default policy is 3 attempts and the schedule is bounded."""

    sleeps: list[float] = []

    def always_fails():
        raise httpx.ConnectError("boom")

    with pytest.raises(httpx.ConnectError):
        retry_call(always_fails, sleep=lambda d: sleeps.append(d))

    # Two sleeps for three attempts.
    assert len(sleeps) == 2
    # All sleeps are finite and within the default ``max_delay``.
    assert all(0.0 <= s <= 8.0 for s in sleeps)
