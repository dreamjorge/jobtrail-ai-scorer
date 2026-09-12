"""Append-only, privacy-safe JobTrail lifecycle events."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

LIFECYCLE_MARKER = "[AI_JOB_LIFECYCLE_V1]"
STATES = ("new", "scored", "reviewing", "prepared", "applied", "interview", "offer", "rejected", "withdrawn")
VALID_TRANSITIONS = {
    "new": ("scored", "rejected", "withdrawn"),
    "scored": ("reviewing", "rejected", "withdrawn"),
    "reviewing": ("prepared", "rejected", "withdrawn"),
    "prepared": ("applied", "rejected", "withdrawn"),
    "applied": ("interview", "rejected", "withdrawn"),
    "interview": ("offer", "rejected", "withdrawn"),
    "offer": ("rejected", "withdrawn"),
    "rejected": (),
    "withdrawn": (),
}
MAX_NOTE = 500
MAX_STRING = 200
_MAX_URL = 500


@dataclass(frozen=True)
class LifecycleEvent:
    schema_version: int
    timestamp: str
    previous_state: str
    new_state: str
    source: str
    source_job_id: str
    source_url: str | None = None
    note: str | None = None
    confirmed: bool | None = None
    provenance: dict[str, str] | None = None


def canonical_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("timestamp must be canonical UTC Z")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be canonical UTC Z") from exc
    if parsed.tzinfo is None or parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
        raise ValueError("timestamp must be canonical UTC Z")
    return value


def _text(value: Any, field: str, limit: int = MAX_STRING, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be a non-blank string of at most {limit} characters")
    return value


def validate_transition(previous_state: str, new_state: str, *, confirm: bool | None = None) -> None:
    if previous_state not in STATES or new_state not in STATES:
        raise ValueError("unknown lifecycle state")
    if new_state not in VALID_TRANSITIONS[previous_state]:
        raise ValueError(f"invalid lifecycle transition: {previous_state} -> {new_state}")
    if new_state == "applied" and confirm is not True:
        raise ValueError("transition to applied requires explicit confirmation")
    if new_state != "applied" and confirm is not None:
        raise ValueError("confirmation is only valid for transitions to applied")


def provenance_from_job(job: Mapping[str, Any]) -> dict[str, str]:
    """Extract only stable, public job identity fields; never copy descriptions."""
    aliases = {
        "title": ("title", "position"), "company": ("company", "companyName"),
        "location": ("location",), "source": ("source",),
        "source_job_id": ("source_job_id", "sourceJobId"),
        "source_url": ("source_url", "sourceUrl", "jobUrl"),
    }
    result: dict[str, str] = {}
    for target, keys in aliases.items():
        value = next((job[k] for k in keys if k in job), None)
        if not isinstance(value, str) or not value.strip() or len(value) > (_MAX_URL if target == "source_url" else MAX_STRING):
            continue
        if target == "source_url":
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
        result[target] = value
    return result


def serialize_lifecycle_event(event: LifecycleEvent | Mapping[str, Any]) -> str:
    data = event.__dict__.copy() if isinstance(event, LifecycleEvent) else dict(event)
    required = {"schema_version", "timestamp", "previous_state", "new_state", "source", "source_job_id"}
    if set(data) - required - {"source_url", "note", "confirmed", "provenance"}:
        raise ValueError("lifecycle event contains unknown fields")
    if data.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    canonical_timestamp(data.get("timestamp"))
    for field in ("previous_state", "new_state", "source", "source_job_id"):
        _text(data.get(field), field)
    if data["previous_state"] not in STATES or data["new_state"] not in STATES:
        raise ValueError("unknown lifecycle state")
    note = _text(data.get("note"), "note", MAX_NOTE, required=False)
    source_url = _text(data.get("source_url"), "source_url", _MAX_URL, required=False)
    if source_url is not None:
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_url must be a valid bounded HTTP(S) URL")
    confirmed = data.get("confirmed")
    if confirmed is not None and not isinstance(confirmed, bool):
        raise ValueError("confirmed must be boolean")
    validate_transition(data["previous_state"], data["new_state"], confirm=confirmed)
    provenance = data.get("provenance")
    if provenance is not None:
        allowed = {"title", "company", "location", "source", "source_job_id", "source_url"}
        if not isinstance(provenance, dict) or set(provenance) - allowed:
            raise ValueError("provenance contains disallowed fields")
        for key, value in provenance.items():
            limit = _MAX_URL if key == "source_url" else MAX_STRING
            _text(value, f"provenance.{key}", limit)
            if key == "source_url":
                parsed = urlparse(value)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("provenance.source_url must be a valid HTTP(S) URL")
        if "source" in provenance and provenance["source"] != data["source"]:
            raise ValueError("source does not match provenance.source")
        if "source_job_id" in provenance and provenance["source_job_id"] != data["source_job_id"]:
            raise ValueError("source_job_id does not match provenance.source_job_id")
        provenance = dict(provenance)
    clean = {"schema_version": 1, "timestamp": data["timestamp"], "previous_state": data["previous_state"], "new_state": data["new_state"], "source": data["source"], "source_job_id": data["source_job_id"]}
    for key, value in (("source_url", source_url), ("note", note), ("confirmed", confirmed), ("provenance", provenance)):
        if value is not None:
            clean[key] = value
    return LIFECYCLE_MARKER + json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_lifecycle_note(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, str) or not body.startswith(LIFECYCLE_MARKER):
        return None
    raw = body[len(LIFECYCLE_MARKER):]
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or serialize_lifecycle_event(data) != body:
            return None
        return data
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def lifecycle_history(job: Mapping[str, Any]) -> list[dict[str, Any]]:
    notes = job.get("notes", [])
    if not isinstance(notes, list):
        return []
    return [parsed for note in notes if isinstance(note, dict) and (parsed := parse_lifecycle_note(note.get("body"))) is not None]


def current_state(job: Mapping[str, Any]) -> str:
    state = "new"
    for event in lifecycle_history(job):
        if event["previous_state"] == state:
            state = event["new_state"]
    return state


def make_lifecycle_event(previous_state: str, new_state: str, *, source: str, source_job_id: str, note: str | None = None, confirm: bool | None = None, source_url: str | None = None, provenance: Mapping[str, Any] | None = None, timestamp: str | None = None) -> str:
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return serialize_lifecycle_event({"schema_version": 1, "timestamp": timestamp, "previous_state": previous_state, "new_state": new_state, "source": source, "source_job_id": source_job_id, "note": note, "confirmed": confirm, "source_url": source_url, "provenance": dict(provenance) if provenance else None})


__all__ = ["LIFECYCLE_MARKER", "STATES", "VALID_TRANSITIONS", "LifecycleEvent", "canonical_timestamp", "current_state", "lifecycle_history", "make_lifecycle_event", "parse_lifecycle_note", "provenance_from_job", "serialize_lifecycle_event", "validate_transition"]
