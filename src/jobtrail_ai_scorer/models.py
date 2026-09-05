"""Structured models for validated AI job scores."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator


NonEmptyString = Annotated[str, StringConstraints(min_length=1)]


class ScoreResult(BaseModel):
    """The required, validated structure returned by a score provider."""

    model_config = ConfigDict(extra="forbid", strict=True)

    score: Annotated[StrictInt, Field(ge=0, le=100)]
    recommendation: Literal["PRIORITY_APPLY", "APPLY", "REVIEW", "SKIP"]
    strengths: list[str]
    gaps: list[str]
    needs_confirmation: list[str]
    hard_requirements_missing: list[str]
    career_value: Literal["High", "Medium", "Low"]
    reasoning: NonEmptyString

    @field_validator("reasoning")
    @classmethod
    def reject_whitespace_only_reasoning(cls, value: str) -> str:
        """Require reasoning to contain at least one non-whitespace character."""

        if not value.strip():
            raise ValueError("reasoning must not be whitespace only")
        return value
