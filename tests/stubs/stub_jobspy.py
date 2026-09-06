"""Fake JobSpy result generator used by the hermetic e2e suite.

The JobSpy search library is the source of the raw job listings that the
JobTrail backend returns from ``/api/discover/search``. The production
orchestrator never imports JobSpy directly; it only consumes the listings
through :class:`jobtrail_ai_scorer.automation.JobTrailHTTPClient.search`.
This stub replaces that generator with an in-process, deterministic list
of listings so the e2e suite never has to spawn a real scraper.

The stub records every search payload it receives and returns the configured
listings as a copy so callers can mutate the result without affecting later
searches. All data is plain Python; no I/O, no network.
"""

from __future__ import annotations

from typing import Any, Iterable


class StubJobSpy:
    """Return a fixed list of JobSpy-style listings from ``search``."""

    def __init__(self, listings: Iterable[dict[str, Any]] | None = None) -> None:
        self._listings: list[dict[str, Any]] = [dict(item) for item in (listings or [])]
        self.calls: list[dict[str, Any]] = []

    def search(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Return a copy of the configured listings and record ``payload``."""

        self.calls.append(dict(payload))
        return [dict(item) for item in self._listings]

    def add(self, listing: dict[str, Any]) -> None:
        """Append a new listing to the generator's output."""

        self._listings.append(dict(listing))
