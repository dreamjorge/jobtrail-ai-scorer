from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Protocol


if TYPE_CHECKING:
    # ``AtsBoardConfig`` lives in ``jobtrail_ai_scorer.automation``; importing it
    # at runtime would create a circular import because ``automation`` already
    # imports from this module. The forward reference is enough for type
    # checking; ``build_ats_adapters`` only inspects the value at runtime.
    from ..automation import AtsBoardConfig


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


def build_ats_adapters(
    ats_boards: "AtsBoardConfig | None",
) -> tuple[SourceAdapter, ...]:
    """Build ATS source adapters from the parsed ``JOB_ATS_BOARDS`` config.

    Returns an empty tuple whenever ``ats_boards`` is ``None`` or contains no
    configured boards (both ``lever_boards`` and ``greenhouse_boards`` empty).
    The factory is the single entry point so
    :class:`jobtrail_ai_scorer.automation.JobTrailAutomation` can build its
    default ``source_adapters`` tuple without importing each adapter module
    directly.

    The ``LeverSourceAdapter`` and ``GreenhouseSourceAdapter`` are appended
    in that order when their respective board lists are non-empty.
    """

    if ats_boards is None:
        return ()
    if not ats_boards.lever_boards and not ats_boards.greenhouse_boards:
        return ()
    # Lazy import: keep ``build_ats_adapters`` cheap for callers that
    # never need the concrete adapter (for example unit tests that only
    # assert on the factory's empty-tuple contract).
    adapters: list[SourceAdapter] = []
    if ats_boards.lever_boards:
        from .lever import LeverSourceAdapter

        adapters.append(LeverSourceAdapter(boards=ats_boards.lever_boards))
    if ats_boards.greenhouse_boards:
        from .greenhouse import GreenhouseSourceAdapter

        adapters.append(GreenhouseSourceAdapter(boards=ats_boards.greenhouse_boards))
    return tuple(adapters)


# Imported here so the types above (NormalizedJob, SourceSearchRequest) are
# already defined when ``adzuna`` and ``jobspy`` are loaded; otherwise a
# circular import would occur because those modules re-import those names
# from this package.
from .adzuna import AdzunaSourceAdapter  # noqa: E402
from .jobspy import JobSpySourceAdapter, normalize_jobspy_job  # noqa: E402
from .lever import LeverSourceAdapter  # noqa: E402
from .greenhouse import GreenhouseSourceAdapter  # noqa: E402

__all__ = [
    "AdzunaSourceAdapter",
    "JobSpySourceAdapter",
    "LeverSourceAdapter",
    "GreenhouseSourceAdapter",
    "NormalizedJob",
    "SearchGateway",
    "SourceAdapter",
    "SourceSearchRequest",
    "build_ats_adapters",
    "normalize_jobspy_job",
]
