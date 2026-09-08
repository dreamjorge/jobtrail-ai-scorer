"""Greenhouse public job-board source adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import httpx

from ..retry import RetryPolicy, retry_call
from . import NormalizedJob, SourceSearchRequest
from .ats_common import _strip_html

SOURCE_NAME = "greenhouse"
DEFAULT_BASE_URL = "https://boards-api.greenhouse.io/v1"
DEFAULT_TIMEOUT = 30.0


class GreenhouseHttpError(Exception):
    """Terminal Greenhouse HTTP or response-shape error."""

    def __init__(self, message: str, *, status_code: int, board: str, response: httpx.Response | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.board = board
        self.response = response


class GreenhouseTransientError(Exception):
    """Transient Greenhouse failure after bounded retries."""

    def __init__(self, message: str, *, attempts: int, board: str, cause: BaseException | None = None):
        super().__init__(message)
        self.attempts = attempts
        self.board = board
        if cause is not None:
            self.__cause__ = cause


def _format_retrieved_at(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    iso = value.astimezone(timezone.utc).isoformat()
    return iso[:-6] + "Z" if iso.endswith("+00:00") else iso


def _location(raw: Mapping[str, Any]) -> str | None:
    value = raw.get("location")
    if not isinstance(value, Mapping):
        return None
    name = value.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _job_type(raw: Mapping[str, Any]) -> str | None:
    for key in ("employment_type", "employmentType", "job_type", "jobType"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def normalize_greenhouse_job(
    raw: Mapping[str, Any], *, board: str, profile_name: str | None, retrieved_at: str | None
) -> NormalizedJob:
    location = _location(raw)
    content = raw.get("content")
    description = _strip_html(content) or None if isinstance(content, str) else None
    title = raw.get("title") if isinstance(raw.get("title"), str) else None
    url = raw.get("absolute_url") if isinstance(raw.get("absolute_url"), str) else None
    remote = bool(location and "remote" in location.lower())
    return NormalizedJob(
        source=SOURCE_NAME,
        source_job_id=str(raw.get("id")) if raw.get("id") is not None else None,
        title=title,
        company=board,
        description=description,
        source_url=url,
        location=location,
        remote=remote,
        job_type=_job_type(raw),
        search_profile=profile_name,
        retrieved_at=retrieved_at,
    )


class GreenhouseSourceAdapter:
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
            raise ValueError("GreenhouseSourceAdapter requires at least one board")
        self.boards = boards
        self.base_url = base_url
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._retry_policy = retry_policy or RetryPolicy()
        self._retry_sleep = retry_sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search(self, request: SourceSearchRequest) -> list[NormalizedJob]:
        cap = max(0, int(request.results_wanted))
        retrieved_at = _format_retrieved_at(self._clock())
        jobs: list[NormalizedJob] = []
        for board in self.boards:
            if len(jobs) >= cap:
                break
            payload = self._get_json(board)
            rows = payload.get("jobs") if isinstance(payload, Mapping) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping) or row.get("id") is None:
                    continue
                jobs.append(normalize_greenhouse_job(row, board=board, profile_name=request.profile_name, retrieved_at=retrieved_at))
                if len(jobs) >= cap:
                    break
        return jobs[:cap]

    def _get_json(self, board: str) -> Mapping[str, Any]:
        url = f"{self.base_url.rstrip('/')}/boards/{board}/jobs"

        def fetch() -> Mapping[str, Any]:
            response = self._client.get(url, params={"content": "true"})
            if 400 <= response.status_code < 500:
                raise GreenhouseHttpError(
                    f"Greenhouse API rejected the request: status={response.status_code}",
                    status_code=response.status_code, board=board, response=response,
                )
            response.raise_for_status()
            if not response.content:
                return {}
            try:
                payload = response.json()
            except ValueError:
                return {}
            return payload if isinstance(payload, Mapping) else {}

        try:
            return retry_call(fetch, policy=self._retry_policy, sleep=self._retry_sleep, label=f"greenhouse search:{board}")
        except GreenhouseHttpError:
            raise
        except Exception as exc:
            metadata = getattr(exc, "retry_metadata", None)
            attempts = metadata.get("attempts") if isinstance(metadata, dict) else None
            attempts = attempts if isinstance(attempts, int) else self._retry_policy.max_attempts
            raise GreenhouseTransientError(
                f"Greenhouse API retries exhausted after {attempts} attempts",
                attempts=attempts, board=board, cause=exc,
            ) from exc


__all__ = [
    "GreenhouseHttpError", "GreenhouseSourceAdapter", "GreenhouseTransientError",
    "normalize_greenhouse_job", "SOURCE_NAME",
]
