"""Tests for the strict opt-in runtime backup cleanup helper (Issue #2).

``scripts/runtime_clean.py`` is the documented cleanup command for the runtime
backup directories that accumulated under the historical rename-on-reinstall
pattern (``scorer-python.backup-<label>-YYYYMMDDTHHMMSSZ``) plus the single
retained ``scorer-python.previous`` backup.

Contract enforced here:

* the helper refuses to remove anything without ``--yes``;
* it removes only the explicitly listed targets, and only when their basename is
  ``scorer-python.previous`` or a dated ``scorer-python.backup-…`` directory;
* any unrelated path (the active install, a CV, a config, a relative path, a
  symlink) refuses the whole run before a single directory is removed.

Every test drives ``tmp_path`` fixtures; no operator runtime file is ever read,
written, or removed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CLEAN = ROOT / "scripts" / "runtime_clean.py"

TARGET_NAME = "scorer-python"
PREVIOUS_NAME = "scorer-python.previous"
DATED_BACKUP_NAME = "scorer-python.backup-automation-20260906T172527Z"
PLAIN_DATED_BACKUP_NAME = "scorer-python.backup-20260906T175517Z"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tree(path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        child = path / rel
        child.parent.mkdir(parents=True, exist_ok=True)
        child.write_text(text, encoding="utf-8")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runtime_root(tmp_path: Path) -> dict[str, Path]:
    """Build a miniature runtime layout with an active install and backups."""

    layout = {
        "active": _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"}),
        "previous": _make_tree(tmp_path / PREVIOUS_NAME, {"pkg/__init__.py": "old\n"}),
        "dated": _make_tree(tmp_path / DATED_BACKUP_NAME, {"pkg/__init__.py": "a\n"}),
        "dated_plain": _make_tree(
            tmp_path / PLAIN_DATED_BACKUP_NAME, {"pkg/__init__.py": "b\n"}
        ),
    }
    cv = tmp_path / "candidate-cv.md"
    cv.write_text("private cv\n", encoding="utf-8")
    layout["cv"] = cv
    return layout


def _run_clean(*args: str, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, str(RUNTIME_CLEAN), *args],
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _targets(*paths: Path) -> list[str]:
    flags: list[str] = []
    for path in paths:
        flags.extend(["--target", str(path)])
    return flags


# ---------------------------------------------------------------------------
# Source-level safety
# ---------------------------------------------------------------------------


def test_clean_helper_exists_with_strict_shebang() -> None:
    assert RUNTIME_CLEAN.is_file(), (
        f"Missing cleanup helper: {RUNTIME_CLEAN.relative_to(ROOT)}. Issue #2 "
        "requires a documented cleanup command."
    )
    text = RUNTIME_CLEAN.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env python3"), (
        "cleanup helper must start with the canonical python3 shebang"
    )


def test_clean_helper_never_shells_out_to_destructive_commands() -> None:
    text = RUNTIME_CLEAN.read_text(encoding="utf-8")
    for pattern in ("rm -rf", "subprocess.run", "subprocess.Popen", "os.system"):
        assert pattern not in text, (
            f"cleanup helper must not use {pattern!r}; it removes only validated "
            "explicit targets through the standard library"
        )


# ---------------------------------------------------------------------------
# Confirmation gate
# ---------------------------------------------------------------------------


def test_clean_requires_yes(tmp_path: Path) -> None:
    layout = _runtime_root(tmp_path)

    result = _run_clean(*_targets(layout["previous"], layout["dated"]))

    assert result.returncode != 0, (
        "cleanup must refuse to act without --yes; stdout="
        f"{result.stdout!r} stderr={result.stderr!r}"
    )
    assert "--yes" in (result.stdout + result.stderr), (
        "the refusal must name the missing --yes flag so the operator knows what "
        f"to do; got stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert layout["previous"].is_dir(), "cleanup removed a backup without --yes"
    assert layout["dated"].is_dir(), "cleanup removed a backup without --yes"


def test_clean_without_yes_reports_the_plan(tmp_path: Path) -> None:
    layout = _runtime_root(tmp_path)

    result = _run_clean(*_targets(layout["previous"]))

    output = result.stdout + result.stderr
    assert str(layout["previous"]) in output, (
        f"the refusal must list the target it would remove; got {output!r}"
    )
    assert layout["previous"].is_dir()


def test_clean_requires_at_least_one_target(tmp_path: Path) -> None:
    _runtime_root(tmp_path)

    result = _run_clean("--yes")

    assert result.returncode != 0, (
        "cleanup with no --target must refuse instead of guessing what to remove; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Removal scope
# ---------------------------------------------------------------------------


def test_clean_only_removes_listed_targets(tmp_path: Path) -> None:
    layout = _runtime_root(tmp_path)

    result = _run_clean(
        *_targets(layout["previous"], layout["dated"]),
        "--yes",
    )

    assert result.returncode == 0, (
        f"cleanup failed; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not layout["previous"].exists(), "listed backup was not removed"
    assert not layout["dated"].exists(), "listed backup was not removed"
    assert layout["active"].is_dir(), "cleanup must never touch the active install"
    assert layout["dated_plain"].is_dir(), (
        "cleanup must never remove a backup that was not listed explicitly"
    )
    assert layout["cv"].is_file(), "cleanup must never touch operator data"


def test_clean_removes_plain_dated_backup(tmp_path: Path) -> None:
    layout = _runtime_root(tmp_path)

    result = _run_clean(*_targets(layout["dated_plain"]), "--yes")

    assert result.returncode == 0, (
        f"cleanup failed; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not layout["dated_plain"].exists()
    assert layout["dated"].is_dir()


def test_clean_is_idempotent_with_missing_ok(tmp_path: Path) -> None:
    layout = _runtime_root(tmp_path)

    first = _run_clean(*_targets(layout["previous"]), "--yes")
    second = _run_clean(*_targets(layout["previous"]), "--yes", "--missing-ok")

    assert first.returncode == 0, f"first cleanup failed; stderr={first.stderr!r}"
    assert second.returncode == 0, (
        "a repeated cleanup with --missing-ok must succeed so the command is safe "
        f"to re-run; stdout={second.stdout!r} stderr={second.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_clean_refuses_unrelated_paths(tmp_path: Path) -> None:
    """The active install and operator data must be refused, even with --yes."""

    layout = _runtime_root(tmp_path)

    active = _run_clean(*_targets(layout["active"]), "--yes")
    assert active.returncode != 0, (
        "cleanup must refuse the active install; stdout="
        f"{active.stdout!r} stderr={active.stderr!r}"
    )
    assert layout["active"].is_dir()

    cv = _run_clean(*_targets(layout["cv"]), "--yes")
    assert cv.returncode != 0, (
        f"cleanup must refuse operator data; stdout={cv.stdout!r} stderr={cv.stderr!r}"
    )
    assert layout["cv"].is_file()


def test_clean_refuses_whole_run_when_one_target_is_unrelated(tmp_path: Path) -> None:
    layout = _runtime_root(tmp_path)

    result = _run_clean(
        *_targets(layout["previous"], layout["active"]),
        "--yes",
    )

    assert result.returncode != 0, (
        "one unrelated target must refuse the whole run; stdout="
        f"{result.stdout!r} stderr={result.stderr!r}"
    )
    assert layout["previous"].is_dir(), (
        "cleanup must validate every target before removing any of them"
    )
    assert layout["active"].is_dir()


def test_clean_refuses_relative_target(tmp_path: Path) -> None:
    layout = _runtime_root(tmp_path)

    result = _run_clean("--target", PREVIOUS_NAME, "--yes", cwd=tmp_path)

    assert result.returncode != 0, (
        "a relative --target must be refused so a stray cwd cannot be cleaned; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert layout["previous"].is_dir()


def test_clean_refuses_symlink(tmp_path: Path) -> None:
    layout = _runtime_root(tmp_path)
    link = tmp_path / "link-root" / PREVIOUS_NAME
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(layout["active"], target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlinks not supported on this platform: {exc}")

    result = _run_clean("--target", str(link), "--yes")

    assert result.returncode != 0, (
        "cleanup must refuse to follow a symlink; stdout="
        f"{result.stdout!r} stderr={result.stderr!r}"
    )
    assert layout["active"].is_dir(), "cleanup followed a symlink into real data"
    assert link.is_symlink(), "cleanup must not remove the symlink either"


def test_clean_refuses_regular_file_with_backup_name(tmp_path: Path) -> None:
    decoy = tmp_path / PREVIOUS_NAME
    decoy.write_text("not a directory\n", encoding="utf-8")

    result = _run_clean("--target", str(decoy), "--yes")

    assert result.returncode != 0, (
        "cleanup removes backup directories only; a regular file must be refused; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert decoy.is_file()


def test_clean_refuses_missing_target_without_missing_ok(tmp_path: Path) -> None:
    result = _run_clean("--target", str(tmp_path / PREVIOUS_NAME), "--yes")

    assert result.returncode != 0, (
        "a missing target must be reported instead of silently succeeding; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_clean_helper_is_importable_from_any_directory(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib.util\n"
                f"spec = importlib.util.spec_from_file_location('rc', {str(RUNTIME_CLEAN)!r})\n"
                "module = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(module)\n"
                "print(module.PREVIOUS_SUFFIX)\n"
            ),
        ],
        cwd=os.fspath(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"helper must import from any cwd; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert ".previous" in result.stdout
