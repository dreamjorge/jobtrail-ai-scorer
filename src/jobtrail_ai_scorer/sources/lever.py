"""Lever source adapter (PR-B).

Implements the optional Lever source adapter behind the existing
:class:`jobtrail_ai_scorer.sources.SourceAdapter` contract. The public
Lever postings endpoint does not require authentication and exposes each
board's postings as a single JSON array at:

    https://api.lever.co/v0/postings/<board>?mode=json

The adapter owns a normalization helper (:func:`normalize_lever_posting`)
that converts a single Lever posting into a :class:`NormalizedJob`, and a
:class:`LeverSourceAdapter` that maps a :class:`SourceSearchRequest` to
one bounded GET per configured board. ``request.search_term`` and
``request.location`` are intentionally ignored — the Lever endpoint scopes
results to a single board slug and does not accept free-text queries. The
adapter uses ``request.profile_name`` for
:attr:`NormalizedJob.search_profile` and ``request.results_wanted`` as the
global cap across boards.

The HTTP transport is injected as an :class:`httpx.Client`. Production
callers pass a real client; tests pass an :class:`httpx.Client` wrapping
an :class:`httpx.MockTransport`. ``RetryPolicy`` and ``retry_sleep`` are
layered around that client.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import httpx

from ..retry import RetryPolicy, retry_call
from . import NormalizedJob, SourceSearchRequest
from .ats_common import _strip_html


SOURCE_NAME = "lever"
DEFAULT_BASE_URL = "https://api.lever.co/v0"
DEFAULT_TIMEOUT = 30.0

LEVER_LOGGER_NAME = "jobtrail_ai_scorer.sources.lever"
_logger = logging.getLogger(LEVER_LOGGER_NAME)


class LeverHttpError(Exception):
    """Terminal HTTP error from the Lever API (raised for ``4xx``).

    Carries ``status_code`` and ``board`` so the retry classifier and the
    orchestrator can identify the failure without depending on this module.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        board: str,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.board = board
        self.response = response


class LeverTransientError(Exception):
    """Raised when bounded retries on transient errors are exhausted.

    Wraps the underlying ``5xx`` or transport error after the retry policy
    is exhausted. Carries ``attempts`` and ``board`` so the orchestrator
    can record the failure without leaking the request URL.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        board: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.board = board
        if cause is not None:
            self.__cause__ = cause


def _format_retrieved_at(value: datetime) -> str:
    """Return ``value`` formatted as a UTC ISO-8601 string with ``Z`` suffix."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    iso = value.isoformat()
    # Rewrite the ``+00:00`` suffix to the canonical ``Z`` form so the
    # timestamp matches the wire format the orchestrator documents.
    if iso.endswith("+00:00"):
        return iso[:-6] + "Z"
    return iso


def _location_from_categories(raw: Mapping[str, Any]) -> str | None:
    """Combine ``categories.location`` and ``categories.commitment`` with ``/``.

    When both are present the helper joins them with `` / ``. When only one
    is present it returns that single value verbatim. When neither is
    present (or ``categories`` is missing/non-mapping) the helper returns
    ``None``.
    """

    categories = raw.get("categories")
    location: str | None = None
    commitment: str | None = None
    if isinstance(categories, Mapping):
        raw_location = categories.get("location")
        raw_commitment = categories.get("commitment")
        if isinstance(raw_location, str) and raw_location.strip():
            location = raw_location.strip()
        if isinstance(raw_commitment, str) and raw_commitment.strip():
            commitment = raw_commitment.strip()
    if location and commitment:
        return f"{location} / {commitment}"
    return location or commitment or None


def normalize_lever_posting(
    raw: Mapping[str, Any],
    *,
    company: str,
    profile_name: str | None,
    retrieved_at: str | None,
) -> NormalizedJob:
    """Translate one raw Lever posting into a :class:`NormalizedJob`.

    Field mapping (per the design table): ``id`` → ``source_job_id``,
    ``text`` → ``title``, ``description`` (HTML stripped) →
    ``description``, ``applyUrl`` → ``source_url``,
    ``categories.location`` + ``categories.commitment`` → ``location``.
    ``profile_name`` and ``retrieved_at`` are wired in by the adapter.
    Optional fields default to ``None`` so they are dropped from the
    import payload by :meth:`NormalizedJob.to_import_payload`.
    """

    raw_id = raw.get("id")
    source_job_id = str(raw_id) if raw_id is not None else None

    raw_title = raw.get("text")
    title = raw_title if isinstance(raw_title, str) else None

    raw_description = raw.get("description")
    if isinstance(raw_description, str):
        description = _strip_html(raw_description) or None
    else:
        description = None

    raw_apply_url = raw.get("applyUrl")
    source_url = raw_apply_url if isinstance(raw_apply_url, str) else None

    return NormalizedJob(
        source=SOURCE_NAME,
        source_job_id=source_job_id,
        title=title,
        company=company,
        description=description,
        source_url=source_url,
        location=_location_from_categories(raw),
        search_profile=profile_name,
        retrieved_at=retrieved_at,
    )


def _build_url(base_url: str, board: str) -> str:
    """Return the absolute URL for one Lever board endpoint."""

    return f"{base_url.rstrip('/')}/postings/{board}"


def _iter_postings(payload: list[Any]) -> list[Mapping[str, Any]]:
    """Return the mapping entries from ``payload`` (skipping non-mappings)."""

    return [item for item in payload if isinstance(item, Mapping)]


class LeverSourceAdapter:
    """Source adapter that translates a ``SourceSearchRequest`` to Lever.

    The constructor accepts an injected :class:`httpx.Client`. When no
    client is provided the adapter creates and owns a real client;
    :meth:`close` then releases it. When the caller supplies a client,
    ownership stays with the caller.

    HTTP error semantics mirror :class:`AdzunaSourceAdapter`:

    * ``4xx`` responses raise :class:`LeverHttpError` immediately
      (terminal, no retry).
    * ``5xx`` and transport errors are retried via :func:`retry_call`.
      Exhausted retries surface as :class:`LeverTransientError` so the
      orchestrator can record a stable failure label without leaking the
      request URL.

    Partial results are preserved: malformed or ``id``-less postings are
    silently skipped; only a full board failure raises.
    """

    name = SOURCE_NAME

    def __init__(
        self,
        boards: tuple[str, ...],
        *,
        client: httpx.Client | None = None,
        base_url: str = DEFAULT_BASE_URL,
        retry_policy: RetryPolicy | None = None,
        retry_sleep: Callable[[float], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(boards, tuple):
            boards = tuple(boards)
        if not boards:
            raise ValueError("LeverSourceAdapter requires at least one board")
        self.boards = boards
        self.base_url = base_url
        if client is None:
            self._owns_client = True
            self._client = httpx.Client(timeout=DEFAULT_TIMEOUT)
        else:
            self._owns_client = False
            self._client = client
        self._retry_policy = retry_policy or RetryPolicy()
        self._retry_sleep = retry_sleep
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))

    def close(self) -> None:
        """Release the underlying ``httpx.Client`` when this adapter owns it."""

        if self._owns_client:
            self._client.close()

    def search(self, request: SourceSearchRequest) -> list[NormalizedJob]:
        """Search all configured boards and return the normalized postings.

        Each board issues exactly one bounded GET; results are concatenated
        in board order. The cap (``request.results_wanted``) is applied
        across the entire batch. ``request.search_term`` and
        ``request.location`` are intentionally ignored.
        """

        retrieved_at = _format_retrieved_at(self._clock())
        cap = max(0, int(request.results_wanted))
        jobs: list[NormalizedJob] = []
        for board in self.boards:
            if len(jobs) >= cap:
                break
            payload = self._get_json(board)
            for posting in _iter_postings(payload):
                job = normalize_lever_posting(
                    posting,
                    company=board,
                    profile_name=request.profile_name,
                    retrieved_at=retrieved_at,
                )
                if job.source_job_id is None:
                    # Postings without an ``id`` cannot be deduplicated or
                    # re-imported; drop them silently.
                    continue
                jobs.append(job)
                if len(jobs) >= cap:
                    break
        return jobs[:cap]

    def _get_json(self, board: str) -> list[Any]:
        """Perform a GET with bounded retries and translate HTTP errors.

        ``4xx`` raises :class:`LeverHttpError` (terminal). ``5xx`` and
        transport errors retry; exhausted retries surface as
        :class:`LeverTransientError`.
        """

        url = _build_url(self.base_url, board)

        def _do_request() -> list[Any]:
            response = self._client.get(url, params={"mode": "json"})

            if 400 <= response.status_code < 500:
                raise LeverHttpError(
                    f"Lever API rejected the request: status={response.status_code}",
                    status_code=response.status_code,
                    board=board,
                    response=response,
                )

            response.raise_for_status()
            # Empty bodies (HTTP 200 with no payload) are partial-failure:
            # the call succeeded but the board returned no postings. Treat
            # as an empty list rather than a terminal error.
            if not response.content:
                return []
            try:
                payload = response.json()
            except ValueError as exc:
                # Malformed JSON is a terminal failure: a broken server
                # response cannot be retried into compliance.
                raise LeverHttpError(
                    "Lever API returned invalid JSON",
                    status_code=response.status_code,
                    board=board,
                    response=response,
                ) from exc
            if not isinstance(payload, list):
                # The Lever endpoint exposes a JSON array; anything else is
                # treated as an empty payload (partial-failure semantics).
                return []
            return payload

        try:
            return retry_call(
                _do_request,
                policy=self._retry_policy,
                sleep=self._retry_sleep,
                logger=_logger,
                label=f"lever search:{board}",
            )
        except LeverHttpError:
            # Terminal classification — propagate without wrapping.
            raise
        except httpx.HTTPError as exc:
            metadata = getattr(exc, "retry_metadata", None)
            attempts = (
                int(metadata["attempts"])
                if isinstance(metadata, dict)
                and isinstance(metadata.get("attempts"), int)
                else self._retry_policy.max_attempts
            )
            _logger.warning(
                "lever: retries exhausted after %d attempts: %s",
                attempts,
                type(exc).__name__,
            )
            raise LeverTransientError(
                f"Lever API retries exhausted after {attempts} attempts",
                attempts=attempts,
                board=board,
                cause=exc,
            ) from exc


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "LEVER_LOGGER_NAME",
    "LeverHttpError",
    "LeverSourceAdapter",
    "LeverTransientError",
    "SOURCE_NAME",
    "normalize_lever_posting",
]
