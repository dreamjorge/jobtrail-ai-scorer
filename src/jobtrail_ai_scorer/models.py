"""Structured models for validated AI job scores."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)


NonEmptyString = Annotated[str, StringConstraints(min_length=1)]


class EvidenceEntry(BaseModel):
    """One explicitly labelled piece of job-match evidence."""

    model_config = ConfigDict(extra="forbid", strict=True)

    label: Literal["direct", "equivalent", "inferred", "missing"]
    text: NonEmptyString

    @field_validator("text")
    @classmethod
    def reject_whitespace_only_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence text must not be whitespace only")
        return value


class ScoreResult(BaseModel):
    """Validated provider result, with additive fit and coverage details."""

    model_config = ConfigDict(extra="forbid", strict=True)

    score: Annotated[StrictInt, Field(ge=0, le=100)]
    recommendation: Literal["PRIORITY_APPLY", "APPLY", "REVIEW", "SKIP"]
    strengths: list[str]
    gaps: list[str]
    needs_confirmation: list[str]
    hard_requirements_missing: list[str]
    career_value: Literal["High", "Medium", "Low"]
    reasoning: NonEmptyString
    fit_score: Annotated[StrictInt, Field(ge=0, le=100)] | None = None
    coverage_score: Annotated[StrictInt, Field(ge=0, le=100)] | None = None
    evidence: list[EvidenceEntry] = Field(default_factory=list)
    exclusion_signals: list[NonEmptyString] = Field(default_factory=list)
    classification: Literal["APPLY", "REVIEW", "EXPLORE", "SKIP"] | None = None

    @field_validator("reasoning")
    @classmethod
    def reject_whitespace_only_reasoning(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reasoning must not be whitespace only")
        return value

    @model_validator(mode="after")
    def apply_compatibility_defaults_and_classification(self) -> "ScoreResult":
        if self.fit_score is None:
            self.fit_score = self.score
        if self.coverage_score is None:
            self.coverage_score = self.score
        # Classification is derived, rather than trusted from model output.
        from .scoring import classify_score

        self.classification = classify_score(
            self.fit_score,
            self.coverage_score,
            exclusion_signal=bool(self.exclusion_signals),
            critical_requirements_missing=self.hard_requirements_missing,
        )
        return self
