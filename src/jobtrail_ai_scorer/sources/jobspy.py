"""JobSpy source adapter.

Encapsulates the adapter and normalization helper that translate JobSpy
payloads (the legacy gateway search response) into ``NormalizedJob`` records
for the :class:`jobtrail_ai_scorer.automation.JobTrailAutomation` orchestrator.

The module was extracted from :mod:`jobtrail_ai_scorer.sources` during PR-A so
the sources package can grow without making ``__init__.py`` a kitchen sink. The
public names (``JobSpySourceAdapter``, ``normalize_jobspy_job``) are re-exported
from ``sources.__init__`` for backward compatibility.
"""

from __future__ import annotations

from typing import Any, Mapping

from . import NormalizedJob, SearchGateway, SourceSearchRequest


SOURCE_NAME = "jobspy"


def normalize_jobspy_job(
    job: Mapping[str, Any], *, profile_name: str | None = None
) -> NormalizedJob:
    """Translate one raw JobSpy payload into a :class:`NormalizedJob`."""

    return NormalizedJob(
        source=job.get("site"),
        source_job_id=job.get("id"),
        title=job.get("title"),
        company=job.get("company"),
        description=job.get("description"),
        source_url=job.get("job_url"),
        location=job.get("location"),
        remote=job.get("is_remote"),
        salary_min=job.get("min_amount"),
        salary_max=job.get("max_amount"),
        salary_currency=job.get("currency"),
        job_type=job.get("job_type"),
        search_profile=profile_name,
    )


class JobSpySourceAdapter:
    """Adapter that maps a ``SourceSearchRequest`` to the legacy JobSpy gateway.

    The adapter is the default ``SourceAdapter`` wired by
    :class:`jobtrail_ai_scorer.automation.JobTrailAutomation` when no explicit
    ``source_adapters`` tuple is supplied.
    """

    name = SOURCE_NAME

    def __init__(self, gateway: SearchGateway) -> None:
        self.gateway = gateway

    def search(self, request: SourceSearchRequest) -> list[NormalizedJob]:
        return [
            normalize_jobspy_job(job, profile_name=request.profile_name)
            for job in self.gateway.search(request.to_jobspy_payload())
        ]


__all__ = [
    "JobSpySourceAdapter",
    "normalize_jobspy_job",
]
