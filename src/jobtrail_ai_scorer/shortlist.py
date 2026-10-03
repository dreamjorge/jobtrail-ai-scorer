"""Pure, fail-closed recommendation eligibility shared by both run modes."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, TypeVar

from .scoring import eligible_score


Job = TypeVar("Job")


def _eligible_score(
    score: Mapping[str, Any], *, score_threshold: int,
) -> dict[str, Any] | None:
    """Validate eligibility without changing the legacy score/ranking policy.

    Callers parse the latest valid legacy note first. A structured note that
    fails eligibility is rejected, never replaced with an older recommendation.
    Explicit SKIP always wins, even over otherwise eligible dual scores.
    """
    return eligible_score(score, score_threshold=score_threshold)


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
