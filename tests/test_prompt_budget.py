"""Tests for bounded and optional candidate context loading."""

import errno
from pathlib import Path

import pytest

from jobtrail_ai_scorer.prompt_budget import (
    PromptBudget,
    clip_with_marker,
    load_optional_text,
)


def test_clip_with_marker_keeps_content_within_budget():
    assert clip_with_marker("abcdefghij", 8, "[CUT]") == "abc[CUT]"


def test_clip_no_change_when_under_budget():
    assert clip_with_marker("short", 20, "[CUT]") == "short"


def test_missing_file_returns_empty_text_and_missing_status(tmp_path):
    text, status = load_optional_text(tmp_path / "missing.md", 20, "[CUT]")

    assert text == ""
    assert status == "missing"


def test_unreadable_file_returns_empty_text_and_unreadable_status(monkeypatch, tmp_path):
    path = tmp_path / "profile.md"
    path.write_text("profile")

    def fail_read_text(self, *args, **kwargs):
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    text, status = load_optional_text(path, 20, "[CUT]")

    assert text == ""
    assert status == "unreadable"


def test_directory_returns_unreadable_status(tmp_path):
    text, status = load_optional_text(tmp_path, 20, "[CUT]")

    assert text == ""
    assert status == "unreadable"


def test_oversized_path_returns_unreadable_status(monkeypatch):
    path = Path("x" * 5000)

    def fail_read_text(self, *args, **kwargs):
        raise OSError(errno.ENAMETOOLONG, "file name too long")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    text, status = load_optional_text(path, 20, "[CUT]")

    assert text == ""
    assert status == "unreadable"


def test_prompt_budget_defaults_and_environment_overrides(monkeypatch):
    monkeypatch.setenv("PROMPT_PROFILE_BUDGET", "101")
    monkeypatch.setenv("PROMPT_CV_BUDGET", "202")
    monkeypatch.setenv("PROMPT_TRUNCATE_MARKER", "[LIMIT]")

    budget = PromptBudget.from_env()

    assert budget.profile_budget_chars == 101
    assert budget.cv_budget_chars == 202
    assert budget.marker == "[LIMIT]"


def test_prompt_budget_rejects_non_positive_budget():
    with pytest.raises(ValueError, match="budget must be positive"):
        PromptBudget(profile_budget_chars=0, cv_budget_chars=20, marker="[CUT]")
