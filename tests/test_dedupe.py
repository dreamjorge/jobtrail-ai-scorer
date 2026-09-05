from jobtrail_ai_scorer.scoring import should_score


def test_skips_empty_description():
    assert should_score({"description": "   "}, force=False) is False


def test_skips_current_marker_unless_forced():
    job = {"description": "A role", "notes": [{"body": "[AI_JOB_SCORE_V1]\n{}"}]}

    assert should_score(job, force=False) is False
    assert should_score(job, force=True) is True


def test_skips_legacy_marker_unless_forced():
    job = {"description": "A role", "notes": [{"body": "Prior [HERMES_JOB_SCORE_V1] score"}]}

    assert should_score(job, force=False) is False
    assert should_score(job, force=True) is True
