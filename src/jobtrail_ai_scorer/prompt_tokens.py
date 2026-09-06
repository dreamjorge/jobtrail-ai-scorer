"""Deterministic prompt token estimation and budget enforcement.

This module provides a lightweight, dependency-free way to approximate the
number of tokens that would be sent to a provider, plus an opt-in warning
when the estimate exceeds a caller-supplied budget. Estimates are deliberately
approximations; the same estimator must always return the same value for the
same input so budget decisions are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Literal

EstimatorName = Literal["chars4", "words"]
PROMPT_TOKEN_ESTIMATOR_ENV = "PROMPT_TOKEN_ESTIMATOR"
PROMPT_TOKEN_BUDGET_ENV = "PROMPT_TOKEN_BUDGET"


class PromptTokenEstimator:
    """Built-in estimator identifiers."""

    CHARS4 = "chars4"
    WORDS = "words"


VALID_ESTIMATORS: tuple[str, ...] = (PromptTokenEstimator.CHARS4, PromptTokenEstimator.WORDS)
DEFAULT_ESTIMATOR: str = PromptTokenEstimator.CHARS4


def estimate_tokens(text: str, *, estimator: EstimatorName | str = DEFAULT_ESTIMATOR) -> int:
    """Estimate the number of prompt tokens in ``text``.

    The estimator is deterministic so the same input always yields the same
    estimate, which keeps the per-section breakdown reproducible run to run.

    - ``chars4`` (default) rounds ``len(text) / 4`` up.
    - ``words`` splits on whitespace and counts tokens.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    name = estimator
    if name not in VALID_ESTIMATORS:
        raise ValueError(
            f"estimator must be one of {VALID_ESTIMATORS!r}, got {estimator!r}"
        )
    if name == PromptTokenEstimator.WORDS:
        return len(text.split())
    # chars4: ceil(len / 4) using integer arithmetic.
    length = len(text)
    return (length + 3) // 4


@dataclass(frozen=True)
class SectionBreakdown:
    """Per-section token estimate plus the summed total."""

    profile: int
    cv: int
    job: int
    schema: int
    instructions: int

    @property
    def total(self) -> int:
        return self.profile + self.cv + self.job + self.schema + self.instructions

    def as_dict(self) -> dict[str, int]:
        return {
            "profile": self.profile,
            "cv": self.cv,
            "job": self.job,
            "schema": self.schema,
            "instructions": self.instructions,
            "total": self.total,
        }


def estimate_sections(
    *,
    profile: str,
    cv: str,
    job: str,
    schema: str,
    instructions: str,
    estimator: EstimatorName | str | None = None,
) -> dict[str, int]:
    """Return per-section token counts for the rendered provider prompt.

    ``estimator`` defaults to the value of ``PROMPT_TOKEN_ESTIMATOR`` when
    unset, falling back to :data:`DEFAULT_ESTIMATOR`.
    """

    chosen = _resolve_estimator(estimator)
    breakdown = SectionBreakdown(
        profile=estimate_tokens(profile, estimator=chosen),
        cv=estimate_tokens(cv, estimator=chosen),
        job=estimate_tokens(job, estimator=chosen),
        schema=estimate_tokens(schema, estimator=chosen),
        instructions=estimate_tokens(instructions, estimator=chosen),
    )
    return breakdown.as_dict()


def optional_budget_from_env(name: str = PROMPT_TOKEN_BUDGET_ENV) -> int | None:
    """Return the positive integer ``PROMPT_TOKEN_BUDGET`` value or ``None``.

    Returns ``None`` when the variable is unset or empty. Rejects zero,
    negative, and non-integer values with ``ValueError`` so callers can fail
    loudly instead of silently accepting a malformed budget.
    """

    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        parsed = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _resolve_estimator(estimator: EstimatorName | str | None) -> EstimatorName:
    """Pick the active estimator: explicit override, env var, then default."""

    if estimator is None:
        env_value = os.environ.get(PROMPT_TOKEN_ESTIMATOR_ENV)
        chosen = env_value if env_value else DEFAULT_ESTIMATOR
    else:
        chosen = estimator
    if chosen not in VALID_ESTIMATORS:
        raise ValueError(
            f"estimator must be one of {VALID_ESTIMATORS!r}, got {chosen!r}"
        )
    return chosen  # type: ignore[return-value]
