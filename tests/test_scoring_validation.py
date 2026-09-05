import pytest
from pydantic import ValidationError

from jobtrail_ai_scorer.models import ScoreResult


def canonical_score(**overrides):
    score = {
        "score": 85,
        "recommendation": "APPLY",
        "strengths": ["Relevant Python experience"],
        "gaps": ["No Kubernetes production experience"],
        "needs_confirmation": ["Remote-work location"],
        "hard_requirements_missing": [],
        "career_value": "High",
        "reasoning": "The role aligns with the candidate's backend experience.",
    }
    score.update(overrides)
    return score


def test_rejects_score_outside_zero_to_one_hundred():
    with pytest.raises(ValidationError):
        ScoreResult.model_validate(canonical_score(score=101))


def test_rejects_unknown_recommendation():
    with pytest.raises(ValidationError):
        ScoreResult.model_validate(canonical_score(recommendation="MAYBE"))


def test_accepts_canonical_score_schema():
    result = ScoreResult.model_validate(canonical_score())

    assert result.model_dump() == canonical_score()


@pytest.mark.parametrize("invalid_score", ["85", 85.0])
def test_rejects_non_integer_scores_without_coercion(invalid_score):
    with pytest.raises(ValidationError):
        ScoreResult.model_validate(canonical_score(score=invalid_score))
