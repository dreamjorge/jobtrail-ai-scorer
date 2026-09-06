"""Bounded retries with exponential backoff for idempotent network calls.

This helper exists so idempotent HTTP and subprocess operations can survive
short-lived transient failures (5xx responses, network blips, write/read
timeouts) without spamming the upstream service or unboundedly delaying the
operator's run. The policy is bounded by ``max_attempts`` and ``max_delay``;
callers opt into jitter when they want to de-correlate concurrent clients.

Retry classification is delegated to :func:`classify_retryable`. The classifier
is intentionally simple: ``5xx`` HTTP errors, transport errors (timeouts,
connection failures), and transient subprocess failures are ``retryable``;
``4xx`` HTTP errors and configuration errors (``ValueError``,
``FileNotFoundError``, ``PermissionError``) are ``terminal``. Callers can
replace ``retry_call`` with a custom implementation when they need finer
control.

The retry helper records every attempt through Python logging with the
structured ``retry:`` prefix so operators can grep for retry storms in the
journal without instrumenting each call site.
"""

from __future__ import annotations

import dataclasses
import logging
import random
import subprocess
import time
from typing import Any, Callable, TypeVar

import httpx

T = TypeVar("T")

#: Structured prefix used in every retry log record. Operators grep for it.
RETRY_LOG_PREFIX = "retry:"

#: Classification labels returned by :func:`classify_retryable`.
RETRYABLE = "retryable"
TERMINAL = "terminal"

_logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry/backoff policy.

    ``max_attempts`` is the total number of attempts including the initial one.
    ``base_delay`` and ``max_delay`` are seconds; ``max_delay`` is a hard cap on
    the per-attempt sleep. ``jitter`` is opt-in (default off) so the schedule
    remains deterministic for tests and operators that want reproducibility.
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: bool = False

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must be non-negative")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be >= base_delay")


def classify_retryable(exc: BaseException) -> str:
    """Return ``"retryable"`` or ``"terminal"`` for the given exception.

    The classifier follows the contract documented in the module docstring:
    HTTP ``5xx``, transport errors, and transient subprocess failures are
    ``retryable``; HTTP ``4xx`` and configuration errors are ``terminal``.

    Exceptions that expose a ``status_code`` attribute (for example the
    :class:`jobtrail_ai_scorer.jobtrail.JobTrailApiError` wrapper) are
    classified by that code via duck typing so this helper does not depend on
    the wrapper module and stays free of circular imports.
    """

    # 4xx / 5xx HTTP status errors are classified by the response code.
    if isinstance(exc, httpx.HTTPStatusError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            if 500 <= status < 600:
                return RETRYABLE
            if 400 <= status < 500:
                return TERMINAL
            return TERMINAL
        # No response attached (rare); default to retryable to give the call a chance.
        return RETRYABLE

    # Duck-typed wrapper exceptions (e.g. ``JobTrailApiError``) expose a
    # ``status_code`` attribute even though they are not httpx exceptions.
    if hasattr(exc, "status_code"):
        wrapper_status = getattr(exc, "status_code", None)
        if isinstance(wrapper_status, int):
            if 500 <= wrapper_status < 600:
                return RETRYABLE
            if 400 <= wrapper_status < 500:
                return TERMINAL
            return TERMINAL
        # ``status_code`` exists but is ``None`` (or otherwise absent a code);
        # treat this as a network/transport wrapper and retry.
        return RETRYABLE

    # Configuration/validation errors are terminal. ``PermissionError`` and
    # ``FileNotFoundError`` are checked here *before* the broad ``OSError``
    # catch below because both inherit from ``OSError`` and would otherwise be
    # misclassified as retryable transport failures.
    if isinstance(exc, (ValueError, TypeError, KeyError, FileNotFoundError, PermissionError)):
        return TERMINAL

    # Transport errors (timeouts, connection failures) are retryable.
    if isinstance(exc, (httpx.HTTPError, TimeoutError, ConnectionError, OSError)):
        return RETRYABLE

    # Subprocess failures are transient: the scorer/provider is idempotent and
    # re-running it with the same inputs is safe.
    if isinstance(exc, subprocess.CalledProcessError):
        return RETRYABLE

    return TERMINAL


def _backoff_delay(
    policy: RetryPolicy,
    attempt_index: int,
    *,
    rng: Callable[[float, float], float] | None = None,
) -> float:
    """Return the delay (seconds) before the next attempt.

    ``attempt_index`` is 0-based: ``0`` is the delay before attempt 2 (after
    the first failure), ``1`` before attempt 3, and so on. The exponential
    schedule ``base_delay * 2 ** attempt_index`` is clamped by ``max_delay``
    so the backoff is always bounded.
    """

    raw = policy.base_delay * (2 ** attempt_index)
    capped = min(raw, policy.max_delay)
    if policy.jitter:
        return max((rng or random.uniform)(0.0, capped), 0.0)
    return max(capped, 0.0)


def retry_call(
    func: Callable[..., T],
    *args: Any,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] | None = None,
    logger: logging.Logger | None = None,
    label: str = "operation",
    **kwargs: Any,
) -> T:
    """Invoke ``func(*args, **kwargs)`` with bounded retries.

    Re-raises the last exception when the policy is exhausted. Uses ``sleep``
    for the backoff timer; defaults to :func:`time.sleep`. Returns the
    callable's result on success.

    The helper annotates the raised exception with ``retry_metadata`` so
    callers can read ``attempts`` and ``classification`` from the original
    exception without wrapping it.
    """

    effective_policy = policy or RetryPolicy()
    effective_sleep: Callable[[float], None] = sleep if sleep is not None else time.sleep
    log = logger if logger is not None else _logger
    attempts_made = 0
    last_exc: BaseException | None = None

    for attempt in range(1, effective_policy.max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            attempts_made = attempt
            classification = classify_retryable(exc)
            if classification != RETRYABLE:
                log.warning(
                    "%s %s attempt=%d classification=%s exc=%s",
                    RETRY_LOG_PREFIX,
                    label,
                    attempt,
                    classification,
                    type(exc).__name__,
                )
                _attach_metadata(exc, attempts_made, classification)
                raise

            if attempt >= effective_policy.max_attempts:
                log.warning(
                    "%s %s attempt=%d classification=exhausted exc=%s",
                    RETRY_LOG_PREFIX,
                    label,
                    attempt,
                    type(exc).__name__,
                )
                last_exc = exc
                break

            delay = _backoff_delay(effective_policy, attempt - 1)
            log.warning(
                "%s %s attempt=%d classification=%s delay=%.3fs exc=%s",
                RETRY_LOG_PREFIX,
                label,
                attempt,
                classification,
                delay,
                type(exc).__name__,
            )
            last_exc = exc
            effective_sleep(delay)

    assert last_exc is not None
    _attach_metadata(last_exc, attempts_made, "exhausted")
    raise last_exc


def _attach_metadata(
    exc: BaseException, attempts: int, classification: str
) -> None:
    """Annotate an exception with retry metadata so callers can introspect it."""

    setattr(
        exc,
        "retry_metadata",
        {"attempts": attempts, "classification": classification},
    )


__all__ = [
    "RETRY_LOG_PREFIX",
    "RETRYABLE",
    "TERMINAL",
    "RetryPolicy",
    "classify_retryable",
    "retry_call",
]
