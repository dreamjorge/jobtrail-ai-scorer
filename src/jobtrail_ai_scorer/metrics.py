"""Pure, privacy-safe funnel metrics computation."""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass(frozen=True)
class SourceStats:
    searched: int = 0
    imported: int = 0
    deduplicated: int = 0
    scored: int = 0


@dataclass(frozen=True)
class ProfileStats:
    searched: int = 0
    imported: int = 0
    deduplicated: int = 0
    scored: int = 0


@dataclass(frozen=True)
class MetricsView:
    period: str
    since: float
    until: float
    searched: int = 0
    imported: int = 0
    deduplicated: int = 0
    scored: int = 0
    scored_failed: int = 0
    notifications: int = 0
    changed: int = 0
    rescored: int = 0
    source_stats: dict[str, SourceStats] = field(default_factory=dict)
    profile_stats: dict[str, ProfileStats] = field(default_factory=dict)
    score_bins: dict[str, int] = field(default_factory=dict)
    matches_above_threshold: int = 0
    application_status: dict[str, int] = field(default_factory=dict)
    last_success_at: float | None = None
    last_failure_at: float | None = None
    missing_data: tuple[str, ...] = ()

    @property
    def notification_count(self) -> int:
        return self.notifications

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["missing_data"] = list(self.missing_data)
        return value


def compute_metrics(
    *,
    runs: Iterable[dict],
    seen_entries: Iterable[dict] = (),
    scored_jobs: Iterable[dict] = (),
    period: str,
    now: float | None = None,
    threshold: int = 80,
) -> MetricsView:
    """Compute bounded metrics from already-collected backend records."""
    if period not in {"today", "7d", "30d"}:
        raise ValueError("period must be today, 7d, or 30d")
    moment = time.time() if now is None else now
    if isinstance(moment, bool) or not isinstance(moment, (int, float)) or not math.isfinite(moment) or moment < 0:
        raise ValueError("now must be a finite, non-negative timestamp")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 100:
        raise ValueError("threshold must be an integer from 0 through 100")
    moment = float(moment)
    if period == "today":
        day = datetime.fromtimestamp(moment, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        since, until = day.timestamp(), (day.timestamp() + 86400)
    else:
        since, until = moment - (7 if period == "7d" else 30) * 86400, moment

    selected_runs = [r for r in runs if isinstance(r, dict) and _in_period(r.get("started_at"), since, until)]
    selected_seen = [e for e in seen_entries if isinstance(e, dict) and _in_period(e.get("first_seen"), since, until)]
    selected_jobs = [j for j in scored_jobs if isinstance(j, dict) and _in_period(j.get("scored_at", j.get("created_at")), since, until, missing_is_valid=True)]

    totals = {key: 0 for key in ("searched", "imported", "deduplicated", "scored", "scored_failed", "changed", "rescored")}
    sources: dict[str, dict[str, int]] = {}
    profiles: dict[str, dict[str, int]] = {}
    notifications = 0
    success_times: list[float] = []
    failure_times: list[float] = []
    missing: set[str] = set()
    for run in selected_runs:
        for key in totals:
            value = run.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] += int(value)
            elif key not in run:
                missing.add(key if key != "scored_failed" else "scored_failed")
        failures = run.get("failures")
        failed = isinstance(failures, list) and bool(failures)
        if run.get("notified") is True:
            notifications += 1
        if "scored_failed" not in run and failed:
            totals["scored_failed"] += sum(
                1 for failure in failures if isinstance(failure, str) and failure.startswith("score:")
            )
        finished = _number(run.get("finished_at"))
        if finished is not None:
            (failure_times if failed else success_times).append(finished)
        _merge_counts(sources, run.get("source_counts"), SourceStats)
        _merge_counts(profiles, run.get("profile_counts"), ProfileStats, duplicates_key="duplicates")
        _merge_run_dimension(sources, run.get("source"), run)
        _merge_run_dimension(profiles, run.get("searchProfile", run.get("profile")), run)

    if not selected_runs:
        missing.update(("searched", "source_counts", "deduplicated", "notifications", "last_success_at", "last_failure_at"))
    elif not any("source_counts" in r for r in selected_runs):
        missing.add("source_counts")
    if not selected_seen and seen_entries:
        missing.add("source_counts")
    valid_seen = [e for e in selected_seen if isinstance(e.get("source"), str) and e.get("source") and _number(e.get("first_seen")) is not None]
    if selected_seen and len(valid_seen) != len(selected_seen):
        missing.add("source_counts")
    if valid_seen:
        # Seen entries are authoritative for imported-by-source counts.
        imported_by_source: dict[str, int] = {}
        for entry in valid_seen:
            source = entry["source"]
            imported_by_source[source] = imported_by_source.get(source, 0) + 1
        for source, count in imported_by_source.items():
            sources.setdefault(source, {})["imported"] = count

    bins = {f"{start}-{start + 9}": 0 for start in range(0, 90, 10)}
    bins["90-100"] = 0
    statuses: dict[str, int] = {}
    for job in selected_jobs:
        score = _score(job)
        if score is not None and 0 <= score <= 100:
            start = 90 if score >= 90 else (score // 10) * 10
            bins[f"{start}-100" if start == 90 else f"{start}-{start + 9}"] += 1
            if score >= threshold:
                totals.setdefault("matches", 0)
                totals["matches"] += 1
        source = job.get("source")
        profile = job.get("searchProfile", job.get("profile"))
        if isinstance(source, str) and source:
            sources.setdefault(source, {})["scored"] = sources.get(source, {}).get("scored", 0) + 1
        if isinstance(profile, str) and profile:
            profiles.setdefault(profile, {})["scored"] = profiles.get(profile, {}).get("scored", 0) + 1
        status = job.get("applicationStatus")
        if isinstance(status, str) and status:
            statuses[status] = statuses.get(status, 0) + 1
    if selected_jobs and not statuses:
        missing.add("application_status")
    if not selected_jobs:
        missing.add("application_status")

    source_stats = {name: SourceStats(**{k: int(v) for k, v in data.items() if k in SourceStats.__dataclass_fields__}) for name, data in sources.items()}
    profile_stats = {name: ProfileStats(**{k: int(v) for k, v in data.items() if k in ProfileStats.__dataclass_fields__}) for name, data in profiles.items()}
    return MetricsView(
        period,
        since,
        until,
        totals["searched"],
        totals["imported"],
        totals["deduplicated"],
        totals["scored"],
        totals["scored_failed"],
        notifications,
        totals["changed"],
        totals["rescored"],
        source_stats,
        profile_stats,
        bins,
        totals.get("matches", 0),
        statuses,
        max(success_times, default=None),
        max(failure_times, default=None),
        tuple(sorted(missing)),
    )


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _in_period(value: Any, since: float, until: float, *, missing_is_valid: bool = False) -> bool:
    number = _number(value)
    return missing_is_valid if number is None else since <= number < until


def _merge_run_dimension(target: dict[str, dict[str, int]], name: Any, run: dict[str, Any]) -> None:
    if not isinstance(name, str) or not name:
        return
    bucket = target.setdefault(name, {})
    for key in ("searched", "imported", "deduplicated", "scored"):
        value = run.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            bucket[key] = bucket.get(key, 0) + int(value)


def _merge_counts(target: dict[str, dict[str, int]], value: Any, _kind: Any, *, duplicates_key: str = "deduplicated") -> None:
    if not isinstance(value, dict):
        return
    for name, counts in value.items():
        if not isinstance(name, str) or not isinstance(counts, dict):
            continue
        bucket = target.setdefault(name, {})
        for key in ("searched", "imported", "deduplicated", "scored"):
            source_key = duplicates_key if key == "deduplicated" else key
            if isinstance(counts.get(source_key), (int, float)) and not isinstance(counts[source_key], bool):
                bucket[key] = bucket.get(key, 0) + int(counts[source_key])


def _score(job: dict[str, Any]) -> int | None:
    value = job.get("score")
    if not isinstance(value, (int, float)) and isinstance(job.get("score_payload"), dict):
        value = job["score_payload"].get("score")
    if not isinstance(value, (int, float)) and isinstance(job.get("notes"), list):
        for note in reversed(job["notes"]):
            body = note.get("body") if isinstance(note, dict) else None
            if isinstance(body, str) and "[AI_JOB_SCORE_V1]" in body:
                try:
                    payload = json.loads(body.split("[AI_JOB_SCORE_V1]", 1)[1].strip())
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("score"), (int, float)):
                    value = payload["score"]
                    break
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


__all__ = ["MetricsView", "SourceStats", "ProfileStats", "compute_metrics"]
