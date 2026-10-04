"""Eligibility, deduplication, and score orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import unicodedata
from typing import Any, Mapping, Protocol

from pydantic import ValidationError

from .models import ScoreResult
from .providers.base import ScoreProvider


CURRENT_MARKER = "[AI_JOB_SCORE_V1]"
LEGACY_MARKER = "[HERMES_JOB_SCORE_V1]"
FINGERPRINT_VERSION = 1
_FINGERPRINT_FIELDS = {
    "title": ("title", "position"),
    "company": ("company",),
    "location": ("location",),
    "description": ("description",),
    "salaryMin": ("salaryMin", "salary_min"),
    "salaryMax": ("salaryMax", "salary_max"),
    "salaryCurrency": ("salaryCurrency", "salary_currency"),
    "jobType": ("jobType", "job_type", "employment_type"),
    "remote": ("remote",),
}


def _normalize_fingerprint_value(value: Any) -> Any:
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFKC", value)
        return " ".join(normalized.casefold().split())
    return value


def job_fingerprint(job: Mapping[str, Any]) -> str:
    """Return the versioned digest of fields that affect a job score."""
    canonical = {
        field: _normalize_fingerprint_value(
            next((job[key] for key in aliases if key in job), None)
        )
        for field, aliases in _FINGERPRINT_FIELDS.items()
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _coerce_int(value: Any) -> int | None:
    """Return ``value`` as ``int`` when it is a real number, otherwise ``None``.

    Booleans are explicitly rejected because Pydantic ``StrictInt`` rejects them
    while Python's ``int(True)`` would silently succeed. The helper keeps the
    parsing logic focused on numeric coercion without leaking Pydantic
    specifics.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return int(value)
    return None


def parse_score_note(
    notes: Any, *, marker: str = CURRENT_MARKER
) -> dict[str, Any] | None:
    """Return the latest valid score payload found in ``notes``.

    The parser is intentionally pure: it depends only on Python builtins. It
    does not touch the JobTrail gateway, the seen cache, or any I/O boundary,
    so the offline automation dry-run slice (see #36) can reuse it on
    captured scenario notes without re-entering the production pipeline.

    A note is considered only when ``body`` is a string that contains
    ``marker``. The JSON payload must be an object whose ``score`` field
    parses as an integer in ``[0, 100]``. When multiple notes match, the
    **last** valid one in the input list wins, matching how the orchestrator
    appends fresh scores after stale ones.

    Returns ``None`` when ``notes`` is malformed, no note carries a valid
    payload, or every candidate payload fails the range check. The schema
    must already be enforced by the score writer; downstream code that needs
    stricter validation can pipe the result through :class:`ScoreResult`.
    """

    if isinstance(notes, str):
        notes = [{"body": notes}]
    if not isinstance(notes, list):
        return None
    markers = (marker, LEGACY_MARKER) if marker == CURRENT_MARKER else (marker,)
    for note in reversed(notes):
        body = note.get("body") if isinstance(note, dict) else None
        matched_marker = next(
            (candidate for candidate in markers if isinstance(body, str) and candidate in body),
            None,
        )
        if matched_marker is None:
            continue
        payload_text = body.split(matched_marker, 1)[1].strip()
        if not payload_text:
            continue
        try:
            value = json.loads(payload_text)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        coerced = _coerce_int(value.get("score"))
        if coerced is None or coerced < 0 or coerced > 100:
            continue
        value["score"] = coerced
        return value
    return None


PROMPT_INSTRUCTIONS = (
    "Evaluate this job against the candidate profile and public strategy context. "
)
PROMPT_SCHEMA = (
    "Return JSON only matching the configured score schema. Use exactly these keys "
    "and no extra fields: score, fit_score, coverage_score, classification, evidence, "
    "exclusion_signals, recommendation, strengths, gaps, needs_confirmation, "
    "hard_requirements_missing, career_value, reasoning. "
    "score must be an integer 0-100. fit_score and coverage_score must be integers 0-100. "
    "classification must be one of APPLY, REVIEW, EXPLORE, SKIP and is derived "
    "deterministically from fit_score, coverage_score, exclusion_signals, and "
    "hard_requirements_missing; any exclusion signal or missing hard requirement "
    "forces SKIP regardless of the scores. Classification is an input only and "
    "will be normalized from these fields. "
    "Each evidence entry must have a non-empty text and exactly one label: "
    "direct, equivalent, inferred, or missing; inferred evidence is not direct. "
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


_MAX_OUTPUT_STRING = 500
_MAX_OUTPUT_ITEMS = 20
_MAX_OUTPUT_ITEM_STRING = 200


def _bound_string(value: str) -> str:
    return value[:_MAX_OUTPUT_STRING]


def _bound_list(values: tuple[str, ...]) -> list[str]:
    return [_bound_string(value[:_MAX_OUTPUT_ITEM_STRING]) for value in values[:_MAX_OUTPUT_ITEMS]]


@dataclass(frozen=True)
class ScoreOutcome:
    """A privacy-safe result for one attempted job."""

    job_id: str
    status: str
    reason: str
    score: int | None = None
    recommendation: str | None = None
    strengths: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    career_value: str | None = None
    reasoning: str | None = None
    hard_requirements_missing: tuple[str, ...] = ()
    needs_confirmation: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {"job_id": _bound_string(self.job_id), "status": _bound_string(self.status)}
        if self.score is not None:
            result.update({"score": self.score, "recommendation": _bound_string(self.recommendation or ""),
                           "strengths": _bound_list(self.strengths), "gaps": _bound_list(self.gaps),
                           "career_value": _bound_string(self.career_value or ""),
                           "reasoning": _bound_string(self.reasoning or ""),
                           "hard_requirements_missing": _bound_list(self.hard_requirements_missing),
                           "needs_confirmation": _bound_list(self.needs_confirmation)})
        result["reason"] = _bound_string(self.reason)
        return {key: result[key] for key in sorted(result)}


@dataclass(frozen=True)
class ScoreRunResult:
    """Counters and per-job outcomes from a scoring run."""

    processed: int
    skipped: int
    failed: int
    outcomes: tuple[ScoreOutcome, ...]

    @property
    def json_output(self) -> list[dict[str, Any]]:
        return [outcome.as_json() for outcome in self.outcomes]

    @property
    def json_text(self) -> str:
        return json.dumps(self.json_output, sort_keys=True, separators=(",", ":"))


def should_score(
    job: dict[str, Any], *, force: bool = False, marker: str = CURRENT_MARKER,
    current_fingerprint: str | None = None,
) -> bool:
    """Return whether a complete job is eligible for scoring."""

    marker = _normalize_marker(marker)
    description = job.get("description")
    if not isinstance(description, str) or not description.strip():
        return False
    if force:
        return True
    return not _contains_score_marker(
        job.get("notes"), marker=marker, current_fingerprint=current_fingerprint
    )


def render_prompt(
    job: dict[str, Any],
    candidate_profile: str,
    strategy_context: dict[str, Any] | str | None = None,
) -> str:
    """Render a deterministic prompt, including only public strategy context."""

    job_json = serialize_job(job)
    public_context = json.dumps(strategy_context, sort_keys=True, default=str) if isinstance(strategy_context, dict) else (strategy_context or "")
    return (
        f"{PROMPT_INSTRUCTIONS}{PROMPT_SCHEMA}\n\n"
        f"Candidate profile:\n{candidate_profile}\n\n"
        f"Public strategy context:\n{public_context}\n\n"
        f"Job:\n{job_json}\n"
    )


FIT_APPLY_THRESHOLD = 70
COVERAGE_APPLY_THRESHOLD = 70
FIT_REVIEW_THRESHOLD = 50
COVERAGE_REVIEW_THRESHOLD = 50


def classify_score(
    fit_score: int,
    coverage_score: int,
    *,
    exclusion_signal: bool = False,
    critical_requirements_missing: list[str] | None = None,
) -> str:
    """Classify strict integer scores using bounded, deterministic thresholds.

    Direct callers receive ``ValueError`` for booleans, non-integers, and values
    outside the inclusive ``0``-``100`` range, matching provider-model strictness.
    """

    if (
        isinstance(fit_score, bool)
        or not isinstance(fit_score, int)
        or isinstance(coverage_score, bool)
        or not isinstance(coverage_score, int)
        or not 0 <= fit_score <= 100
        or not 0 <= coverage_score <= 100
    ):
        raise ValueError("scores must be strict integers between 0 and 100")
    if exclusion_signal or critical_requirements_missing:
        return "SKIP"
    if fit_score >= FIT_APPLY_THRESHOLD and coverage_score >= COVERAGE_APPLY_THRESHOLD:
        return "APPLY"
    if (fit_score >= FIT_REVIEW_THRESHOLD and coverage_score >= COVERAGE_REVIEW_THRESHOLD) or fit_score >= FIT_APPLY_THRESHOLD:
        return "REVIEW"
    # Adjacent roles remain worth exploring when evidence coverage is strong,
    # not merely because fit is moderate despite absent evidence.
    if coverage_score >= COVERAGE_REVIEW_THRESHOLD:
        return "EXPLORE"
    return "SKIP"


def eligible_score(
    score: Mapping[str, Any], *, score_threshold: int,
) -> dict[str, Any] | None:
    """Return a normalized eligible copy, preserving legacy score ranking.

    Apply this after parsing the latest valid note; rejection must never fall
    back to an older note. Any additive marker requires both strict dual scores
    and explicit safety arrays. Genuine legacy notes need no additive defaults.
    Neither this helper nor its callers rewrite persisted notes.
    """

    legacy_score = score.get("score")
    if (
        isinstance(legacy_score, bool)
        or not isinstance(legacy_score, int)
        or not 0 <= legacy_score <= 100
        or legacy_score < score_threshold
    ):
        return None
    for field in ("recommendation", "classification"):
        if field in score:
            value = score[field]
            if not isinstance(value, str) or value.strip().upper() == "SKIP":
                return None
    if "classification" in score and score["classification"] not in (
        "APPLY", "REVIEW", "EXPLORE",
    ):
        return None

    additive = any(field in score for field in (
        "fit_score", "coverage_score", "classification", "evidence", "exclusion_signals",
    ))
    for field in ("hard_requirements_missing", "exclusion_signals"):
        if field not in score:
            if additive:
                return None
            continue
        value = score[field]
        if not isinstance(value, list) or not all(
            isinstance(entry, str) and entry.strip() for entry in value
        ):
            return None
        if value:
            return None

    normalized = dict(score)
    if additive:
        try:
            classification = classify_score(score.get("fit_score"), score.get("coverage_score"))
        except ValueError:
            return None
        if classification == "SKIP":
            return None
        normalized["classification"] = classification
    return normalized


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
    strategy_context: dict[str, Any] | str | None = None,
    output_json: bool = False,
    job_payload: dict[str, Any] | None = None,
) -> ScoreRunResult:
    """Score independently eligible jobs and save only validated results."""

    if output_json and not dry_run:
        raise ValueError("output_json requires dry_run")
    marker = _normalize_marker(marker)
    candidates = [job_payload] if job_payload is not None else _select_candidates(client, job_id=job_id, limit=limit)
    outcomes: list[ScoreOutcome] = []
    processed = skipped = failed = 0

    for candidate in candidates:
        candidate_id = candidate.get("id")
        if job_payload is not None and not isinstance(candidate_id, str):
            candidate_id = job_id
        if not isinstance(candidate_id, str) or not candidate_id:
            failed += 1
            outcomes.append(ScoreOutcome("<unknown>", "failed", "invalid_job_id"))
            continue
        if not _has_description(candidate):
            skipped += 1
            outcomes.append(ScoreOutcome(candidate_id, "skipped", "empty_description"))
            continue

        try:
            full_job = candidate if job_payload is not None else client.get_job(candidate_id)
            current_fingerprint = job_fingerprint(full_job)
            if not should_score(
                full_job, force=force, marker=marker,
                current_fingerprint=current_fingerprint,
            ):
                skipped += 1
                reason = "empty_description" if not _has_description(full_job) else "already_scored"
                outcomes.append(ScoreOutcome(candidate_id, "skipped", reason))
                continue

            prompt = render_prompt(full_job, candidate_profile, strategy_context)
            raw_score = provider.score(prompt)
            decoded_score = _decode_provider_score(raw_score)
            score = ScoreResult.model_validate(decoded_score)
            note_body = _serialize_note(
                marker,
                score,
                include_additive=any(
                    key in decoded_score
                    for key in ("fit_score", "coverage_score", "evidence", "exclusion_signals", "classification")
                ),
                job=full_job,
            )
            if dry_run:
                processed += 1
                outcomes.append(_validated_outcome(candidate_id, score) if output_json else ScoreOutcome(candidate_id, "dry_run", "validated"))
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


def _validated_outcome(job_id: str, score: ScoreResult) -> ScoreOutcome:
    return ScoreOutcome(job_id, "dry_run", "validated", score.score, score.recommendation,
                       tuple(score.strengths), tuple(score.gaps), score.career_value,
                       score.reasoning, tuple(score.hard_requirements_missing),
                       tuple(score.needs_confirmation))


_MAX_PROVIDER_RESPONSE = 1_000_000


def _decode_provider_score(raw_score: str) -> dict[str, Any]:
    """Decode one provider JSON object, optionally wrapped in a JSON fence."""

    if not isinstance(raw_score, str):
        raise TypeError("provider score must be a string")
    if len(raw_score) > _MAX_PROVIDER_RESPONSE:
        raise json.JSONDecodeError("provider response exceeds maximum size", "", 0)

    payload = raw_score.strip()
    if payload.startswith("```json"):
        if not payload.endswith("```"):
            raise json.JSONDecodeError("unterminated JSON fence", payload, 0)
        payload = payload[len("```json") : -len("```")].strip()

    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise json.JSONDecodeError("expected a JSON object", payload, 0)
    return decoded


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


def _contains_score_marker(
    notes: Any, *, marker: str, current_fingerprint: str | None = None
) -> bool:
    if not isinstance(notes, list):
        return False
    if marker == CURRENT_MARKER:
        for note in notes:
            body = note.get("body") if isinstance(note, dict) else None
            if isinstance(body, str) and LEGACY_MARKER in body:
                return True
    for note in reversed(notes):
        body = note.get("body") if isinstance(note, dict) else None
        if not isinstance(body, str) or marker not in body:
            continue
        payload = parse_score_note([{"body": body}], marker=marker)
        if payload is None or current_fingerprint is None:
            return True
        recorded = payload.get("input_fingerprint")
        if not isinstance(recorded, str):
            return True
        return recorded == current_fingerprint
    return False


def serialize_score_note(
    marker: str, score: ScoreResult | Mapping[str, Any], *, job: Mapping[str, Any] | None = None
) -> str:
    payload = score.model_dump(mode="json") if isinstance(score, ScoreResult) else dict(score)
    if job is not None:
        payload["fingerprint_version"] = FINGERPRINT_VERSION
        payload["input_fingerprint"] = job_fingerprint(job)
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{marker}\n{canonical_json}"


def _serialize_note(
    marker: str, score: ScoreResult, *, include_additive: bool = True,
    job: Mapping[str, Any] | None = None,
) -> str:
    payload = score.model_dump(mode="json")
    if not include_additive:
        for key in ("fit_score", "coverage_score", "evidence", "exclusion_signals", "classification"):
            payload.pop(key, None)
    return serialize_score_note(marker, payload, job=job)
