"""Synchronous HTTP client for the JobTrail API."""

from typing import Any

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
        self._http_client = http_client or httpx.Client(timeout=10.0)

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return the jobs exposed by JobTrail."""

        return self._request("GET", "/api/jobs")

    def get_job(self, job_id: str) -> dict[str, Any]:
        """Return one JobTrail job by identifier."""

        return self._request("GET", f"/api/jobs/{job_id}")

    def add_note(self, job_id: str, body: str) -> None:
        """Create a note with ``body`` on a JobTrail job."""

        self._request("POST", f"/api/jobs/{job_id}/notes", json={"body": body})

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http_client.request(method, f"{self._base_url}{path}", **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise JobTrailApiError(f"JobTrail API request failed: {method} {path}") from error
        return response.json() if response.content else None
