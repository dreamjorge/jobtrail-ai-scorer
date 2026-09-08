from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class SourceSearchRequest:
    sites: tuple[str, ...]
    search_term: str
    location: str
    results_wanted: int
    hours_old: int
    is_remote: bool
    profile_name: str = "default"

    def to_jobspy_payload(self) -> dict[str, Any]:
        return {
            "sites": list(self.sites),
            "searchTerm": self.search_term,
            "location": self.location,
            "resultsWanted": self.results_wanted,
            "hoursOld": self.hours_old,
            "isRemote": self.is_remote,
        }


@dataclass(frozen=True)
class NormalizedJob:
    source: str | None
    source_job_id: str | None
    title: str | None = None
    company: str | None = None
    description: str | None = None
    source_url: str | None = None
    location: str | None = None
    remote: bool | None = None
    salary_min: int | float | None = None
    salary_max: int | float | None = None
    salary_currency: str | None = None
    job_type: str | None = None
    search_profile: str | None = None
    retrieved_at: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> tuple[str | None, str | None]:
        return self.source, self.source_job_id

    def to_import_payload(self) -> dict[str, Any]:
        values = {
            "source": self.source,
            "sourceJobId": self.source_job_id,
            "company": self.company,
            "position": self.title,
            "description": self.description,
            "jobUrl": self.source_url,
            "location": self.location,
            "remote": self.remote,
            "salaryMin": self.salary_min,
            "salaryMax": self.salary_max,
            "salaryCurrency": self.salary_currency,
            "jobType": self.job_type,
            "searchProfile": self.search_profile,
            "retrievedAt": self.retrieved_at,
            "metadata": dict(self.metadata) if self.metadata else None,
        }
        return {key: value for key, value in values.items() if value is not None}


class SourceAdapter(Protocol):
    name: str

    def search(self, request: SourceSearchRequest) -> list[NormalizedJob]: ...


class SearchGateway(Protocol):
    def search(self, payload: dict[str, Any]) -> list[Mapping[str, Any]]: ...


def normalize_jobspy_job(
    job: Mapping[str, Any], *, profile_name: str | None = None
) -> NormalizedJob:
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
    name = "jobspy"

    def __init__(self, gateway: SearchGateway) -> None:
        self.gateway = gateway

    def search(self, request: SourceSearchRequest) -> list[NormalizedJob]:
        return [
            normalize_jobspy_job(job, profile_name=request.profile_name)
            for job in self.gateway.search(request.to_jobspy_payload())
        ]
