"""Adzuna source adapter.

This module implements the optional Adzuna source adapter behind the
existing :class:`jobtrail_ai_scorer.sources.SourceAdapter` contract.

The adapter owns:

* A normalization helper that converts a single Adzuna result record
  into a :class:`NormalizedJob`.
* An :class:`AdzunaConfig` dataclass that loads credentials from the
  environment with explicit disable semantics, returning ``None`` to
  signal silent-disable for :class:`AutomationConfig` callers.
* An :class:`AdzunaSourceAdapter` that maps a
  :class:`SourceSearchRequest` to a single-page Adzuna GET request and
  normalizes each ``results`` entry.

The HTTP transport is injected as an :class:`httpx.Client`. Production
callers pass a real client (no transport override); tests pass an
:class:`httpx.Client` wrapping an :class:`httpx.MockTransport` so the
suite stays hermetic. Credentials, retry policy, and disable semantics
are layered around that client.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import httpx

from ..retry import RetryPolicy, retry_call
from . import NormalizedJob, SourceSearchRequest


SOURCE_NAME = "adzuna"
DEFAULT_BASE_URL = "https://api.adzuna.com/v1"
MAX_PER_PAGE = 50
DEFAULT_TIMEOUT = 30.0

ADZUNA_LOGGER_NAME = "jobtrail_ai_scorer.sources.adzuna"
_logger = logging.getLogger(ADZUNA_LOGGER_NAME)


class AdzunaHttpError(Exception):
    """Terminal HTTP error from the Adzuna API.

    Raised immediately (no retry) for 4xx responses. Carries the
    ``status_code`` attribute so
    :func:`jobtrail_ai_scorer.retry.classify_retryable` classifies it as
    terminal via duck typing without a hard dependency on this module.
    ``response`` is kept for diagnostic purposes; it is the
    :class:`httpx.Response` instance that produced the error.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class AdzunaTransientError(Exception):
    """Raised when bounded retries on transient errors are exhausted.

    Wraps the underlying 5xx or transport error after the retry policy has
    been exhausted. Carries ``attempts`` so the orchestrator can include
    the number of tries in the failure label without leaking the original
    error message (which can contain secrets from URLs).
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        if cause is not None:
            self.__cause__ = cause


# Mapping of public Adzuna API country codes (ISO 3166-1 alpha-2 lower-case)
# to ISO 4217 currency codes. The mapping is intentionally conservative;
# unknown country codes resolve to ``None`` so the salary currency is simply
# omitted from the normalized payload.
_COUNTRY_TO_CURRENCY: Mapping[str, str] = {
    "us": "USD",
    "gb": "GBP",
    "uk": "GBP",  # legacy alias kept for safety
    "mx": "MXN",
    "ca": "CAD",
    "au": "AUD",
    "nz": "NZD",
    "de": "EUR",
    "fr": "EUR",
    "es": "EUR",
    "it": "EUR",
    "nl": "EUR",
    "be": "EUR",
    "at": "EUR",
    "ie": "EUR",
    "pl": "PLN",
    "ch": "CHF",
    "in": "INR",
    "sg": "SGD",
    "za": "ZAR",
    "br": "BRL",
}


def _display_name(container: Any) -> str | None:
    """Return ``container['display_name']`` when ``container`` is a mapping."""

    if isinstance(container, Mapping):
        value = container.get("display_name")
        return value if isinstance(value, str) else None
    return None


def _combine_contract_fields(raw: Mapping[str, Any]) -> str | None:
    contract_type = raw.get("contract_type")
    contract_time = raw.get("contract_time")

    if contract_type and contract_time:
        return f"{contract_type}/{contract_time}"
    if isinstance(contract_type, str):
        return contract_type
    if isinstance(contract_time, str):
        return contract_time
    return None


def country_to_currency(country: str | None) -> str | None:
    """Return the ISO 4217 currency code for a known Adzuna country.

    Unknown or empty inputs yield ``None`` so the caller can simply omit
    ``salary_currency`` from the import payload. The lookup is case-folded
    because Adzuna uses lower-case ISO 3166-1 alpha-2 codes by convention.
    """

    if not isinstance(country, str):
        return None
    normalized = country.strip().lower()
    if not normalized:
        return None
    return _COUNTRY_TO_CURRENCY.get(normalized)


def normalize_adzuna_job(
    raw: Mapping[str, Any],
    *,
    profile_name: str | None = None,
    salary_currency: str | None = None,
) -> NormalizedJob:
    """Translate one Adzuna result record into a ``NormalizedJob``.

    Optional fields default to ``None`` so they are dropped from the
    import payload by ``NormalizedJob.to_import_payload()``.

    ``salary_currency`` is wired in by the adapter so the configured
    country drives the currency code without leaking secrets into the
    payload or the log stream.
    """

    raw_id = raw.get("id")
    source_job_id = str(raw_id) if raw_id is not None else None

    return NormalizedJob(
        source=SOURCE_NAME,
        source_job_id=source_job_id,
        title=raw.get("title") if isinstance(raw.get("title"), str) else None,
        company=_display_name(raw.get("company")),
        description=(
            raw.get("description")
            if isinstance(raw.get("description"), str)
            else None
        ),
        source_url=(
            raw.get("redirect_url")
            if isinstance(raw.get("redirect_url"), str)
            else None
        ),
        location=_display_name(raw.get("location")),
        salary_min=raw.get("salary_min"),
        salary_max=raw.get("salary_max"),
        salary_currency=salary_currency,
        job_type=_combine_contract_fields(raw),
        search_profile=profile_name,
        retrieved_at=(
            raw.get("created") if isinstance(raw.get("created"), str) else None
        ),
    )


def _truthy(value: str | None) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AdzunaConfig:
    """Resolved Adzuna configuration sourced from the process environment.

    The dataclass holds the resolved credentials, country, base URL, and an
    explicit ``enabled`` flag so :class:`AutomationConfig` and tests can treat
    the disabled case with a single ``None`` check from
    :meth:`from_env`. Constructing an ``AdzunaConfig`` directly bypasses the
    env loading logic; production callers should use :meth:`from_env`.
    """

    app_id: str
    app_key: str
    country: str = "us"
    base_url: str = DEFAULT_BASE_URL
    enabled: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AdzunaConfig | None":
        """Build an :class:`AdzunaConfig` from ``env`` (defaults to ``os.environ``).

        The contract is intentionally silent: when Adzuna is disabled (no
        credentials, no disable flag) the method returns ``None``. The single
        exception is when only one of ``ADZUNA_APP_ID`` / ``ADZUNA_APP_KEY`` is
        supplied — in that case a warning is logged with the exception type so
        operators can diagnose the misconfiguration without leaking either
        secret value into the log stream.
        """

        e = os.environ if env is None else env

        if _truthy(e.get("JOB_DISABLE_ADZUNA")):
            return None

        app_id = e.get("ADZUNA_APP_ID", "").strip()
        app_key = e.get("ADZUNA_APP_KEY", "").strip()

        if not app_id and not app_key:
            # Silent disable: no credentials configured at all.
            return None

        if not app_id or not app_key:
            # Partial credentials: warn with the exception type only.
            # The warning intentionally carries the exception class name so
            # operators can grep for the misconfiguration without ever
            # leaking the secret values into the log stream.
            _logger.warning(
                "adzuna config disabled: %s", ValueError.__name__
            )
            return None

        country_raw = (e.get("ADZUNA_COUNTRY") or "").strip().lower()
        country = country_raw or "us"
        base_url = (e.get("ADZUNA_BASE_URL") or "").strip() or DEFAULT_BASE_URL

        return cls(
            app_id=app_id,
            app_key=app_key,
            country=country,
            base_url=base_url,
            enabled=True,
        )


def _build_search_params(
    config: AdzunaConfig, request: SourceSearchRequest
) -> dict[str, str]:
    """Translate a ``SourceSearchRequest`` into an Adzuna GET query.

    The mapping pins the contract enforced by the integration tests:
    ``what``, ``where``, ``results_per_page`` (capped at ``MAX_PER_PAGE``),
    ``max_days_old``, ``app_id``, ``app_key``. The country is interpolated
    into the URL path because the documented Adzuna endpoint
    ``/v1/jobs/{country}/search/1`` requires it there.
    """

    return {
        "app_id": config.app_id,
        "app_key": config.app_key,
        "what": request.search_term,
        "where": request.location,
        "results_per_page": str(min(request.results_wanted, MAX_PER_PAGE)),
        "max_days_old": str(request.hours_old),
    }


class AdzunaSourceAdapter:
    """Source adapter that translates a ``SourceSearchRequest`` to Adzuna.

    The constructor accepts an injected :class:`httpx.Client` so callers
    (including tests) can swap in a fixture-driven transport. When no
    client is provided the adapter creates and owns a real
    :class:`httpx.Client`; :meth:`close` then releases it. When the
    caller supplies a client, ownership stays with the caller and the
    adapter never closes it.

    Credentials and country are sourced from :class:`AdzunaConfig` so the
    adapter derives ``salary_currency`` from the configured country
    without touching the raw record.

    The HTTP layer uses the existing :class:`RetryPolicy` so transient
    5xx and transport failures retry with bounded attempts. 4xx
    responses raise :class:`AdzunaHttpError` immediately (terminal), and
    exhausted retries surface as :class:`AdzunaTransientError` so the
    orchestrator can record a stable failure label without leaking the
    request URL (which carries credentials).
    """

    name = SOURCE_NAME

    def __init__(
        self,
        config: AdzunaConfig,
        *,
        client: httpx.Client | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        if client is None:
            self._owns_client = True
            self._client = httpx.Client(timeout=DEFAULT_TIMEOUT)
        else:
            self._owns_client = False
            self._client = client
        self._retry_policy = retry_policy or RetryPolicy()
        self._retry_sleep = retry_sleep
        self.salary_currency = country_to_currency(config.country)

    def close(self) -> None:
        """Release the underlying ``httpx.Client`` when this adapter owns it."""

        if self._owns_client:
            self._client.close()

    def search(self, request: SourceSearchRequest) -> list[NormalizedJob]:
        params = _build_search_params(self.config, request)
        url_path = f"/jobs/{self.config.country}/search/1"

        payload = self._get_json(url_path, params)
        # Normalize the response. Missing ``results`` or malformed items
        # must not fail the whole call (partial-failure semantics from
        # the source adapter contract).
        if not isinstance(payload, Mapping):
            return []
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            return []
        return [
            normalize_adzuna_job(
                item,
                profile_name=request.profile_name,
                salary_currency=self.salary_currency,
            )
            for item in raw_results
            if isinstance(item, Mapping)
        ]

    def _get_json(
        self, url_path: str, params: Mapping[str, str]
    ) -> Mapping[str, Any]:
        """Perform a GET with bounded retries and translate HTTP errors.

        4xx responses raise :class:`AdzunaHttpError` (terminal, no retry).
        5xx and transport errors are retried via :func:`retry_call`.
        When the retry policy is exhausted, :class:`AdzunaTransientError`
        is raised so the orchestrator can record a stable failure label
        without leaking the request URL (which carries credentials).
        """

        base_url = self.config.base_url.rstrip("/")
        url = f"{base_url}{url_path}"

        def _do_request() -> Mapping[str, Any]:
            response = self._client.get(url, params=params)

            if 400 <= response.status_code < 500:
                # 4xx is terminal. The message intentionally omits the
                # URL (which contains credentials) and only includes the
                # status code and a static label.
                raise AdzunaHttpError(
                    f"Adzuna API rejected the request: status={response.status_code}",
                    status_code=response.status_code,
                    response=response,
                )

            # Let ``response.raise_for_status()`` produce the standard
            # ``httpx.HTTPStatusError`` for 5xx so retry_call's existing
            # classifier handles it without a special case.
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                # Malformed JSON is treated as a terminal failure: a
                # broken server response cannot be retried into
                # compliance by additional attempts.
                raise AdzunaHttpError(
                    "Adzuna API returned invalid JSON",
                    status_code=response.status_code,
                    response=response,
                ) from exc
            # Non-object top-level payloads are a partial-failure case:
            # we cannot construct any ``results`` entry from them, but the
            # call itself succeeded. Preserve the legacy silent fallback
            # so the orchestrator still sees an empty job list rather than
            # a hard HTTP error.
            if not isinstance(payload, Mapping):
                return {}
            return payload

        try:
            return retry_call(
                _do_request,
                policy=self._retry_policy,
                sleep=self._retry_sleep,
                logger=_logger,
                label="adzuna search",
            )
        except AdzunaHttpError:
            # Terminal classification — propagate without wrapping so the
            # orchestrator can classify it as ``terminal`` (no retry).
            raise
        except httpx.HTTPError as exc:
            # 5xx exhausted retries, or transport error after exhaustion.
            # The retry helper attaches ``retry_metadata`` to ``exc``;
            # fall back to the policy cap when the metadata is missing.
            metadata = getattr(exc, "retry_metadata", None)
            attempts = (
                int(metadata["attempts"])
                if isinstance(metadata, dict)
                and isinstance(metadata.get("attempts"), int)
                else self._retry_policy.max_attempts
            )
            _logger.warning(
                "adzuna: retries exhausted after %d attempts: %s",
                attempts,
                type(exc).__name__,
            )
            raise AdzunaTransientError(
                f"Adzuna API retries exhausted after {attempts} attempts",
                attempts=attempts,
                cause=exc,
            ) from exc


__all__ = [
    "ADZUNA_LOGGER_NAME",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "MAX_PER_PAGE",
    "AdzunaConfig",
    "AdzunaHttpError",
    "AdzunaSourceAdapter",
    "AdzunaTransientError",
    "SOURCE_NAME",
    "country_to_currency",
    "normalize_adzuna_job",
]