from pathlib import Path

import pytest

from jobtrail_ai_scorer.matching_strategy import (
    REQUIRED_SECTIONS,
    MatchingStrategy,
    load_matching_strategy,
)


VALID_MARKDOWN = """# Public matching strategy

## Target roles
Backend and platform engineering.

## Required signals
Production Python and service ownership.

## Equivalent skills
Go or Java experience is equivalent.

## Adjacent roles
Developer tooling and SRE.

## Exclusion rules
Exclude roles requiring relocation or clearance.

## Location and seniority tolerance
Remote first; one level either way.

## Evidence policy
Use only job requirements and this public strategy.
"""


def test_loader_returns_stable_strategy_sections(tmp_path):
    path = tmp_path / "matching-strategy.md"
    path.write_text(VALID_MARKDOWN)

    strategy = load_matching_strategy(path)

    assert isinstance(strategy, MatchingStrategy)
    assert strategy.available is True
    assert strategy.status == "loaded"
    assert tuple(strategy.sections) == REQUIRED_SECTIONS
    assert strategy.sections["Target roles"] == "Backend and platform engineering."
    assert "Candidate CV" not in strategy.prompt_context


@pytest.mark.parametrize(
    "bad_text",
    [
        VALID_MARKDOWN.replace("## Evidence policy", "## Other policy"),
        VALID_MARKDOWN + "\nCandidate CV: private history",
    ],
)
def test_loader_safely_rejects_invalid_or_private_content(tmp_path, bad_text):
    path = tmp_path / "matching-strategy.md"
    path.write_text(bad_text)

    strategy = load_matching_strategy(path)

    assert strategy.available is False
    assert strategy.sections == {}
    assert strategy.prompt_context == ""
    assert strategy.status in {"invalid", "forbidden"}


def test_loader_safely_rejects_missing_and_oversized_content(tmp_path):
    missing = load_matching_strategy(tmp_path / "missing.md")
    oversized_path = tmp_path / "large.md"
    oversized_path.write_text(VALID_MARKDOWN)
    oversized = load_matching_strategy(oversized_path, max_chars=10)

    assert missing.status == "missing"
    assert missing.available is False
    assert oversized.status == "oversized"
    assert oversized.available is False


def test_loader_rejects_forbidden_private_path_marker(tmp_path):
    strategy = load_matching_strategy(tmp_path / "candidate-cv.md")

    assert strategy.status == "forbidden"
    assert strategy.available is False


def test_loader_reports_unreadable_directory(tmp_path):
    strategy = load_matching_strategy(tmp_path)

    assert strategy.status == "unreadable"
    assert strategy.available is False


@pytest.mark.parametrize(
    "marker",
    [
        "n8n workflow details",
        "Send results to an n8n webhook",
        "private integration details",
        "private API key material",
    ],
)
def test_loader_rejects_private_integration_markers(tmp_path, marker):
    path = tmp_path / "strategy.md"
    path.write_text(VALID_MARKDOWN + f"\n{marker}.")

    strategy = load_matching_strategy(path)

    assert strategy.status == "forbidden"
    assert strategy.available is False


def test_loader_rejects_n8n_path_marker(tmp_path):
    strategy = load_matching_strategy(tmp_path / "n8n-workflow.md")

    assert strategy.status == "forbidden"
    assert strategy.available is False


def test_loader_reads_only_bounded_content(tmp_path, monkeypatch):
    path = tmp_path / "strategy.md"
    path.write_text("x" * 100)
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *_args, **_kwargs: pytest.fail("oversized content was loaded wholesale"),
    )

    strategy = load_matching_strategy(path, max_chars=10)

    assert strategy.status == "oversized"
    assert strategy.available is False
