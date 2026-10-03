"""Pure recommendation eligibility and compatibility policy."""

import pytest

from jobtrail_ai_scorer.shortlist import build_shortlist


def shortlist(scores, threshold=80):
    return build_shortlist(
        [(str(index), score) for index, score in enumerate(scores)],
        score_threshold=threshold,
    )


@pytest.mark.parametrize("patch", [
    {"recommendation": "SKIP"},
    {"classification": "SKIP"},
    {"hard_requirements_missing": ["license"]},
    {"exclusion_signals": ["excluded"]},
    {"hard_requirements_missing": None},
    {"hard_requirements_missing": ""},
    {"exclusion_signals": False},
    {"exclusion_signals": {}},
    {"exclusion_signals": [False]},
    {"hard_requirements_missing": [3]},
    {"fit_score": 99},
    {"fit_score": None, "coverage_score": 99},
    {"fit_score": "99", "coverage_score": 99},
    {"fit_score": True, "coverage_score": 99},
    {"fit_score": 99.0, "coverage_score": 99},
    {"fit_score": 101, "coverage_score": 99},
    {"fit_score": 20, "coverage_score": 20, "classification": "APPLY"},
    {"fit_score": 99, "coverage_score": 99, "classification": "SKIP"},
    {"fit_score": 99, "coverage_score": 99, "recommendation": "SKIP"},
    {"fit_score": 99, "coverage_score": 99, "exclusion_signals": ["excluded"]},
    {"fit_score": 99, "coverage_score": 99, "hard_requirements_missing": ["license"]},
    {"fit_score": 99, "coverage_score": 99, "exclusion_signals": None},
])
def test_shortlist_fails_closed(patch):
    assert shortlist([{"score": 99, **patch}]) == ()


@pytest.mark.parametrize("fit,coverage,classification", [
    (90, 90, "APPLY"), (60, 60, "REVIEW"), (30, 60, "EXPLORE"),
])
def test_shortlist_derives_classification_without_lowering_legacy_threshold(
    fit, coverage, classification,
):
    score = {"score": 80, "fit_score": fit, "coverage_score": coverage,
             "classification": "forged", "hard_requirements_missing": [],
             "exclusion_signals": []}
    result = shortlist([score])
    assert result[0][1]["classification"] == classification
    assert score["classification"] == "forged"  # pure: no input mutation
    assert shortlist([{**score, "score": 79}]) == ()


def test_shortlist_legacy_threshold_cap_and_stable_ties():
    scores = [{"score": value} for value in (80, 91, 91, 90, 79)]
    assert [job for job, _ in shortlist(scores)] == ["1", "2", "3"]
    assert shortlist([{"score": 79}]) == ()
    assert shortlist([{"score": 80}]) == (("0", {"score": 80}),)
    assert shortlist([]) == ()
