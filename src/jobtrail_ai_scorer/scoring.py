"""Eligibility, deduplication, and score orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol

from pydantic import ValidationError

from .models import ScoreResult
from .providers.base import ScoreProvider

CURRENT_MARKER = "[AI_JOB_SCORE_V1]"
LEGACY_MARKER = "[HERMES_JOB_SCORE_V1]"

PROMPT_INSTRUCTIONS = (
    "Evaluate this job against the candidate profile. "
)
PROMPT_SCHEMA = (
    "Return JSON only matching the configured score schema. Use exactly these keys "
    "and no extra fields: score, recommendation, strengths, gaps, needs_confirmation, "
    "hard_requirements_missing, career_value, reasoning. "
    "score must be an integer 0-100. "
    "recommendation must be one of PRIORITY_APPLY, APPLY, REVIEW, SKIP. "
    "strengths, gaps, needs_confirmation, and hard_requirements_missing "
    "must be arrays of strings. "
    "career_value must be one of High, Medium, Low. "
    "reasoning must be a non-empty string."
)


def serialize_job(job: dict[str, Any]) -> str:
    """Return the deterministic JSON serialization of ``job`` (excluding notes)."""

    job_data = {key: value for key, value in job.items() if key != "notes"}
    return json.dumps(job_data, sort_keys=True, default=str)


class JobTrailGateway(Protocol):
    """The small JobTrail boundary required by the scoring workflow."""

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return job summaries."""

    def get_job(self, job_id: str) -> dict[str, Any]:
        """Return a complete job record."""

    def add_note(self, job_id: str, body: str) -> None:
        """Add a note to a job."""


@dataclass(frozen=True)
class ScoreOutcome:
    """A privacy-safe result for one attempted job."""

    job_id: str
    status: str
    reason: str


@dataclass(frozen=True)
class ScoreRunResult:
    """Counters and per-job outcomes from a scoring run."""

    processed: int
    skipped: int
    failed: int
    outcomes: tuple[ScoreOutcome, ...]


def should_score(
    job: dict[str, Any], *, force: bool = False, marker: str = CURRENT_MARKER
) -> bool:
    """Return whether a complete job is eligible for scoring."""

    marker = _normalize_marker(marker)
    description = job.get("description")
    if not isinstance(description, str) or not description.strip():
        return False
    if force:
        return True
    return not _contains_score_marker(job.get("notes"), marker=marker)


def render_prompt(job: dict[str, Any], candidate_profile: str) -> str:
    """Render a deterministic provider prompt from a job and local profile."""

    job_json = serialize_job(job)
    return (
        f"{PROMPT_INSTRUCTIONS}{PROMPT_SCHEMA}\n\n"
        f"Candidate profile:\n{candidate_profile}\n\n"
        f"Job:\n{job_json}\n"
    )


def score_jobs(
    client: JobTrailGateway,
    provider: ScoreProvider,
    candidate_profile: str,
    *,
    job_id: str | None = None,
    limit: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    marker: str = CURRENT_MARKER,
    emit_status: bool = True,
) -> ScoreRunResult:
    """Score independently eligible jobs and save only validated results."""

    marker = _normalize_marker(marker)
    candidates = _select_candidates(client, job_id=job_id, limit=limit)
    outcomes: list[ScoreOutcome] = []
    processed = skipped = failed = 0

    for candidate in candidates:
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or not candidate_id:
            failed += 1
            outcomes.append(ScoreOutcome("<unknown>", "failed", "invalid_job_id"))
            continue
        if not _has_description(candidate):
            skipped += 1
            outcomes.append(ScoreOutcome(candidate_id, "skipped", "empty_description"))
            continue

        try:
            full_job = client.get_job(candidate_id)
            if not should_score(full_job, force=force, marker=marker):
                skipped += 1
                reason = "empty_description" if not _has_description(full_job) else "already_scored"
                outcomes.append(ScoreOutcome(candidate_id, "skipped", reason))
                continue

            prompt = render_prompt(full_job, candidate_profile)
            raw_score = provider.score(prompt)
            score = ScoreResult.model_validate(json.loads(raw_score))
            note_body = _serialize_note(marker, score)
            if dry_run:
                processed += 1
                outcomes.append(ScoreOutcome(candidate_id, "dry_run", "validated"))
                if emit_status:
                    print(f"DRY RUN {candidate_id}: validated score {score.score}")
            else:
                client.add_note(candidate_id, note_body)
                processed += 1
                outcomes.append(ScoreOutcome(candidate_id, "saved", "saved"))
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            failed += 1
            outcomes.append(ScoreOutcome(candidate_id, "failed", "invalid_score"))
            if emit_status:
                print(f"FAILED {candidate_id}: invalid score ({error.__class__.__name__})")
        except Exception as error:  # Independent jobs must continue after boundary errors.
            failed += 1
            outcomes.append(ScoreOutcome(candidate_id, "failed", "error"))
            if emit_status:
                print(f"FAILED {candidate_id}: {error.__class__.__name__}")

    return ScoreRunResult(processed, skipped, failed, tuple(outcomes))


def _select_candidates(
    client: JobTrailGateway, *, job_id: str | None, limit: int | None
) -> list[dict[str, Any]]:
    if job_id is not None:
        return [{"id": job_id, "description": "selected job"}]
    candidates = client.list_jobs()
    return candidates if limit is None else candidates[:limit]


def _normalize_marker(marker: str) -> str:
    if not isinstance(marker, str):
        raise ValueError("marker must not be empty or whitespace")
    normalized = marker.strip()
    if not normalized:
        raise ValueError("marker must not be empty or whitespace")
    return normalized


def _has_description(job: dict[str, Any]) -> bool:
    description = job.get("description")
    return isinstance(description, str) and bool(description.strip())


def _contains_score_marker(notes: Any, *, marker: str) -> bool:
    if not isinstance(notes, list):
        return False
    for note in notes:
        body = note.get("body") if isinstance(note, dict) else None
        if isinstance(body, str) and (marker in body or LEGACY_MARKER in body):
            return True
    return False


def _serialize_note(marker: str, score: ScoreResult) -> str:
    canonical_json = json.dumps(
        score.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return f"{marker}\n{canonical_json}"
