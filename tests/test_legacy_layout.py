"""Tests for the legacy scripts layout (Issue #8).

The repository historically shipped a small batch dry-run wrapper
(``scripts/run-scorer.example.sh``) and a Hermes ``job-search`` dry-run
wrapper (``scripts/hermes-score-jobs.sh``) alongside the canonical
real-path flow at ``scripts/automated-job-search.example.py``,
``scripts/hermes-docker-wrapper.example.sh`` and
``scripts/notify-whatsapp-via-hermes.example.sh``. Issue #8 deprecates the
dry-run wrappers and groups them under ``scripts/legacy/`` so operators
can find them without confusing them with the canonical flow.

These tests enforce that contract:

* ``scripts/legacy/`` exists as a directory.
* Every script under ``scripts/legacy/`` starts with a ``DEPRECATED`` header
  and contains the literal phrase ``do not use in production`` so the
  deprecation is impossible to miss when the file is opened.
* The canonical flow scripts remain at the ``scripts/`` root and are the
  only ``*.sh`` / ``*.py`` files allowed there (no misfiled legacy script
  escapes the directory move).
* ``README.md`` and ``docs/runtime-automation.md`` both reference
  ``scripts/legacy/`` so operators can locate the deprecation notice.
"""

from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
LEGACY_DIR = SCRIPTS_DIR / "legacy"
LEGACY_README = LEGACY_DIR / "README.md"
README = ROOT / "README.md"
RUNTIME_AUTOMATION = ROOT / "docs" / "runtime-automation.md"

# Canonical flow / helper scripts that are allowed to live at scripts/ root.
# Anything else under scripts/ must live under scripts/legacy/ or be removed.
CANONICAL_SCRIPTS: tuple[Path, ...] = (
    SCRIPTS_DIR / "_purge_runtime_example.py",
    SCRIPTS_DIR / "automated-job-search.example.py",
    SCRIPTS_DIR / "hermes-docker-wrapper.example.sh",
    SCRIPTS_DIR / "notify-whatsapp-via-hermes.example.sh",
)

# Legacy dry-run wrappers that must live under scripts/legacy/ with a
# DEPRECATED header.
LEGACY_SCRIPTS: tuple[Path, ...] = (
    LEGACY_DIR / "run-scorer.example.sh",
    LEGACY_DIR / "hermes-score-jobs.sh",
)

DEPRECATED_HEADER = "DEPRECATED"
DEPRECATED_PROHIBITED_PHRASE = "do not use in production"
LEGACY_DIR_NAME = "scripts/legacy"


def test_legacy_layout_directory_exists() -> None:
    """Issue #8 requires ``scripts/legacy/`` to group deprecated examples."""

    assert LEGACY_DIR.is_dir(), (
        f"Missing legacy directory: {LEGACY_DIR}. Issue #8 requires grouping "
        "deprecated dry-run scripts under scripts/legacy/."
    )


@pytest.mark.parametrize("legacy_script", LEGACY_SCRIPTS)
def test_legacy_script_has_deprecated_header(legacy_script: Path) -> None:
    """Every legacy script must be present, declare DEPRECATED and refuse production."""

    assert legacy_script.is_file(), (
        f"Legacy script missing: {legacy_script}. Move it from scripts/ root "
        "and prepend the DEPRECATED header."
    )
    text = legacy_script.read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:30])
    assert DEPRECATED_HEADER in head, (
        f"{legacy_script.relative_to(ROOT)} must include the {DEPRECATED_HEADER!r} "
        f"marker in its first 30 lines so the deprecation is visible at a glance; "
        f"got head={head!r}"
    )
    assert DEPRECATED_PROHIBITED_PHRASE in text, (
        f"{legacy_script.relative_to(ROOT)} must contain the literal phrase "
        f"{DEPRECATED_PROHIBITED_PHRASE!r} so operators know the script must "
        f"not be used in production"
    )


@pytest.mark.parametrize("canonical_script", CANONICAL_SCRIPTS)
def test_all_canonical_scripts_present(canonical_script: Path) -> None:
    """The canonical flow scripts must remain at the ``scripts/`` root."""

    assert canonical_script.is_file(), (
        f"Canonical script missing from scripts/ root: "
        f"{canonical_script.relative_to(ROOT)}. The repo must keep shipping "
        "this active flow / helper script."
    )


def test_no_legacy_script_under_root() -> None:
    """Only canonical scripts may live at the ``scripts/`` root."""

    found: set[Path] = set()
    for pattern in ("*.sh", "*.py"):
        for path in sorted(SCRIPTS_DIR.glob(pattern)):
            # Skip anything that already lives inside scripts/legacy/.
            if LEGACY_DIR in path.parents:
                continue
            found.add(path)

    expected = set(CANONICAL_SCRIPTS)
    missing = expected - found
    extra = found - expected

    assert not missing, (
        "Canonical scripts missing from scripts/ root: "
        f"{sorted(str(p.relative_to(ROOT)) for p in missing)}"
    )
    assert not extra, (
        "Unexpected *.sh/*.py script found at scripts/ root (should live under "
        f"{LEGACY_DIR_NAME}/ or be removed): "
        f"{sorted(str(p.relative_to(ROOT)) for p in extra)}"
    )


def test_legacy_readme_exists() -> None:
    """``scripts/legacy/README.md`` must document the deprecation."""

    assert LEGACY_README.is_file(), (
        f"Missing legacy README: {LEGACY_README}. Add a short note explaining "
        "why these scripts are deprecated and what replaces them."
    )


def test_readme_references_legacy() -> None:
    """``README.md`` must reference ``scripts/legacy/`` so operators can locate it."""

    text = README.read_text(encoding="utf-8")
    assert LEGACY_DIR_NAME in text, (
        f"{README.relative_to(ROOT)} must reference {LEGACY_DIR_NAME!r} so "
        "operators can locate the deprecated dry-run scripts."
    )


def test_docs_references_legacy() -> None:
    """``docs/runtime-automation.md`` must reference ``scripts/legacy/``."""

    text = RUNTIME_AUTOMATION.read_text(encoding="utf-8")
    assert LEGACY_DIR_NAME in text, (
        f"{RUNTIME_AUTOMATION.relative_to(ROOT)} must reference "
        f"{LEGACY_DIR_NAME!r} so operators can locate the deprecated dry-run "
        "scripts from the runtime automation guide."
    )