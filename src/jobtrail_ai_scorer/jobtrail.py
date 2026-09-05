"""Synchronous HTTP client for the JobTrail API."""

from types import TracebackType
from typing import Any
from urllib.parse import quote

import httpx


class JobTrailApiError(Exception):
    """Raised when JobTrail cannot be reached or returns an HTTP error."""


class JobTrailClient:
    """Small synchronous wrapper around the JobTrail jobs and notes endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(timeout=10.0)

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return the jobs exposed by JobTrail."""

        payload = self._request_json("GET", "/api/jobs")
        if not isinstance(payload, list) or not all(isinstance(job, dict) for job in payload):
            raise JobTrailApiError("JobTrail API returned an invalid jobs collection")
        return payload

    def get_job(self, job_id: str) -> dict[str, Any]:
        """Return one JobTrail job by identifier."""

        payload = self._request_json("GET", self._job_path(job_id))
        if not isinstance(payload, dict):
            raise JobTrailApiError("JobTrail API returned an invalid job")
        return payload

    def add_note(self, job_id: str, body: str) -> None:
        """Create a note with ``body`` on a JobTrail job."""

        self._request("POST", f"{self._job_path(job_id)}/notes", json={"body": body})

    def close(self) -> None:
        """Close the HTTP client when this instance created it."""

        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> "JobTrailClient":
        """Return this client for use as a context manager."""

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close an owned HTTP client when its context exits."""

        self.close()

    @staticmethod
    def _job_path(job_id: str) -> str:
        return f"/api/jobs/{quote(job_id, safe='')}"

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http_client.request(method, f"{self._base_url}{path}", **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise JobTrailApiError(f"JobTrail API request failed: {method} {path}") from error
        return response

    def _request_json(self, method: str, path: str) -> Any:
        response = self._request(method, path)
        try:
            return response.json()
        except ValueError as error:
            raise JobTrailApiError(f"JobTrail API returned invalid JSON: {method} {path}") from error
