"""Validation and privacy-safe serialization for score feedback notes."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

FEEDBACK_MARKER = "[AI_JOB_FEEDBACK_V1]"
FEEDBACK_SCORER_VERSION = "0.1.0"
FEEDBACK_LABELS = (
    "good_match", "false_positive", "too_senior", "too_junior",
    "wrong_location", "missing_skill", "other",
)
MAX_FEEDBACK_COMMENT = 500
MAX_FEEDBACK_METADATA = 100


def _normalize_metadata(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise ValueError(f"{field} must not contain control characters")
    if len(normalized) > MAX_FEEDBACK_METADATA:
        raise ValueError(f"{field} must be at most 100 characters")
    return normalized


def normalize_comment(comment: Any) -> str | None:
    if comment is None:
        return None
    if not isinstance(comment, str):
        raise ValueError("comment must be a string")
    value = " ".join(unicodedata.normalize("NFKC", comment).split())
    if not value:
        return None
    if len(value) > MAX_FEEDBACK_COMMENT:
        raise ValueError("comment must be at most 500 characters")
    return value


def validate_labels(labels: Any) -> tuple[str, ...]:
    if isinstance(labels, str) or not isinstance(labels, Iterable):
        raise ValueError("labels must be a non-empty collection")
    values = list(labels)
    if not values or len(values) > len(FEEDBACK_LABELS):
        raise ValueError("labels must contain between 1 and 7 labels")
    if any(not isinstance(label, str) or label not in FEEDBACK_LABELS for label in values):
        raise ValueError("invalid feedback label")
    selected = set(values)
    return tuple(label for label in FEEDBACK_LABELS if label in selected)


def _source(job: Mapping[str, Any] | None) -> str:
    if job is not None:
        for key in ("source", "sourceName"):
            if key in job and job[key] is not None:
                return _normalize_metadata(job[key], "source")
    return "manual"


def _validate_timestamp(timestamp: Any) -> str:
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("timestamp must be a non-empty string")
    if not timestamp.endswith("Z"):
        raise ValueError("timestamp must be canonical UTC ISO-8601 ending in Z")
    try:
        parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("timestamp must be canonical UTC ISO-8601 ending in Z") from error
    timespec = "microseconds" if "." in timestamp else "seconds"
    canonical = parsed.isoformat(timespec=timespec).replace("+00:00", "Z")
    if canonical != timestamp:
        raise ValueError("timestamp must be canonical UTC ISO-8601 ending in Z")
    return timestamp


def build_feedback_payload(
    labels: Any, *, comment: Any = None, job: Mapping[str, Any] | None = None,
    timestamp: str | None = None, scorer_version: str = FEEDBACK_SCORER_VERSION,
) -> dict[str, Any]:
    selected = validate_labels(labels)
    normalized_comment = normalize_comment(comment)
    normalized_scorer_version = _normalize_metadata(scorer_version, "scorer_version")
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    timestamp = _validate_timestamp(timestamp)
    payload: dict[str, Any] = {
        "labels": list(selected), "scorer_version": normalized_scorer_version,
        "source": _source(job), "timestamp": timestamp,
    }
    if normalized_comment is not None:
        payload["comment"] = normalized_comment
    return payload


def serialize_feedback_note(payload: Mapping[str, Any]) -> str:
    """Validate and serialize only the bounded public feedback schema."""
    if not isinstance(payload, Mapping):
        raise ValueError("feedback payload must be an object")
    canonical = build_feedback_payload(
        payload.get("labels"), comment=payload.get("comment"),
        timestamp=payload.get("timestamp"), job={"source": payload.get("source")},
        scorer_version=payload.get("scorer_version", FEEDBACK_SCORER_VERSION),
    )
    return f"{FEEDBACK_MARKER}\n{json.dumps(canonical, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}"


def parse_feedback_note(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, str):
        return None
    lines = body.split("\n", 1)
    if len(lines) != 2 or lines[0].strip() != FEEDBACK_MARKER:
        return None
    try:
        payload = json.loads(lines[1])
        if not isinstance(payload, dict):
            return None
        canonical = build_feedback_payload(
            payload.get("labels"), comment=payload.get("comment"),
            timestamp=payload.get("timestamp"),
            job={"source": payload.get("source")},
            scorer_version=payload.get("scorer_version"),
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if set(payload) != set(canonical):
        return None
    return canonical if payload == canonical else None


def latest_feedback_for_job(job: Mapping[str, Any]) -> dict[str, Any] | None:
    notes = job.get("notes")
    if not isinstance(notes, list):
        return None
    for note in reversed(notes):
        parsed = parse_feedback_note(note.get("body") if isinstance(note, Mapping) else None)
        if parsed is not None:
            return parsed
    return None


def feedback_note_body(labels: Any, *, comment: Any = None, job: Mapping[str, Any] | None = None) -> str:
    return serialize_feedback_note(build_feedback_payload(labels, comment=comment, job=job))


__all__ = [
    "FEEDBACK_MARKER", "FEEDBACK_LABELS", "FEEDBACK_SCORER_VERSION",
    "MAX_FEEDBACK_COMMENT", "build_feedback_payload", "feedback_note_body",
    "latest_feedback_for_job", "normalize_comment", "parse_feedback_note",
    "serialize_feedback_note", "validate_labels",
]
