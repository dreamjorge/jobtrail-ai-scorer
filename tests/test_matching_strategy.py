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


def test_default_strategy_is_independent_of_cwd(tmp_path, monkeypatch):
    from jobtrail_ai_scorer.config import AppConfig

    monkeypatch.chdir(tmp_path)
    config = AppConfig(jobtrail_base_url="http://localhost:8000", candidate_profile_path="synthetic.md")
    strategy = load_matching_strategy(config.matching_strategy_path)
    assert strategy.available
    assert "Backend" in strategy.sections["Target roles"]
    assert load_matching_strategy().available
    explicit = tmp_path / "explicit.md"
    explicit.write_text(VALID_MARKDOWN)
    assert load_matching_strategy(explicit).sections["Target roles"] == "Backend and platform engineering."
    assert load_matching_strategy(tmp_path / "missing.md").status == "missing"


def test_wheel_contains_cwd_independent_default_and_preserves_override(tmp_path):
    """Build and extract locally; all intermediates remain in pytest's tmp_path."""
    import os
    import shutil
    import subprocess
    import sys
    import zipfile

    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    shutil.copyfile(root / "pyproject.toml", source / "pyproject.toml")
    package = root / "src" / "jobtrail_ai_scorer"
    for path in (*package.rglob("*.py"), *package.glob("data/*.md")):
        target = source / "src" / "jobtrail_ai_scorer" / path.relative_to(package)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    temp = tmp_path / "temp"
    temp.mkdir()
    env = dict(os.environ, TMPDIR=str(temp), PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONDONTWRITEBYTECODE="1")
    wheels = tmp_path / "wheels"
    command = [sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation", "--no-cache-dir",
               "--wheel-dir", str(wheels), "."]
    build = subprocess.run(command, cwd=source, env=env, text=True, capture_output=True)
    assert build.returncode == 0, build.stdout + build.stderr
    wheel, = wheels.glob("*.whl")
    target = tmp_path / "target"
    with zipfile.ZipFile(wheel) as archive:
        assert "jobtrail_ai_scorer/data/matching-strategy.md" in archive.namelist()
        archive.extractall(target)
    cwd = tmp_path / "empty-cwd"
    cwd.mkdir()
    override = cwd / "rules.md"
    override.write_text(VALID_MARKDOWN)
    script = '''
import sys
sys.path.insert(0, sys.argv[1])
from jobtrail_ai_scorer.config import AppConfig
from jobtrail_ai_scorer.matching_strategy import load_matching_strategy
config = AppConfig(jobtrail_base_url="http://localhost:8000", candidate_profile_path="synthetic.md")
assert load_matching_strategy().available
assert load_matching_strategy(config.matching_strategy_path).available
config = AppConfig(jobtrail_base_url="http://localhost:8000", candidate_profile_path="synthetic.md", matching_strategy_path="rules.md")
assert load_matching_strategy(config.matching_strategy_path).sections["Target roles"] == "Backend and platform engineering."
assert load_matching_strategy("missing.md").status == "missing"
'''
    check = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(target)], cwd=cwd, env=env, text=True, capture_output=True)
    assert check.returncode == 0, check.stderr


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
