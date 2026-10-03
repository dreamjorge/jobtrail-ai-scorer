"""Pure, fail-closed recommendation eligibility shared by both run modes."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, TypeVar

from .scoring import classify_score


Job = TypeVar("Job")


def _eligible_score(
    score: Mapping[str, Any], *, score_threshold: int,
) -> dict[str, Any] | None:
    """Validate eligibility without changing the legacy score/ranking policy.

    Callers parse the latest valid legacy note first. A structured note that
    fails eligibility is rejected, never replaced with an older recommendation.
    Explicit SKIP always wins, even over otherwise eligible dual scores.
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
        value = score.get(field)
        if isinstance(value, str) and value.strip().upper() == "SKIP":
            return None
    for field in ("hard_requirements_missing", "exclusion_signals"):
        if field in score:
            value = score[field]
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                return None
            if value:
                return None

    normalized = dict(score)
    if "fit_score" in score or "coverage_score" in score:
        try:
            classification = classify_score(
                score.get("fit_score"), score.get("coverage_score"),
            )
        except ValueError:
            return None
        if classification == "SKIP":
            return None
        normalized["classification"] = classification
    elif "classification" in score and score["classification"] not in (
        "APPLY", "REVIEW", "EXPLORE",
    ):
        return None
    return normalized


def build_shortlist(
    candidates: Iterable[tuple[Job, Mapping[str, Any]]], *, score_threshold: int,
) -> tuple[tuple[Job, dict[str, Any]], ...]:
    """Return at most three eligible jobs in descending legacy-score order.

    Stable sorting retains discovery order on ties. APPLY, REVIEW and EXPLORE
    are all eligible subject to the unchanged legacy threshold; no new class
    priority or fit/coverage ranking is introduced.
    """
    eligible = []
    for job, score in candidates:
        normalized = _eligible_score(score, score_threshold=score_threshold)
        if normalized is not None:
            eligible.append((job, normalized))
    eligible.sort(key=lambda item: -item[1]["score"])
    return tuple(eligible[:3])
