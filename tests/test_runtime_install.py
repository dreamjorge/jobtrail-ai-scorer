"""Tests for the idempotent runtime install wrapper (Issue #2).

``scripts/runtime_install.py`` is the operator-facing entry point for
reinstalling the runtime ``scorer-python`` tree. It stages the new content with
``pip install --target <staging>`` (opt-in, operator-run only) and then hands the
staged tree to ``scripts/runtime_backup.rotate`` so that:

* a byte-identical reinstall changes nothing;
* a changed reinstall rotates exactly one ``<target>.previous`` backup;
* a byte-identical ``.previous`` is never overwritten.

These tests never run ``pip``, never reach the network, and never touch operator
runtime files: the staged tree is always a plain ``tmp_path`` directory passed
with ``--staged``, and the pip branch is only ever asserted in dry-run (planning)
mode.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_INSTALL = ROOT / "scripts" / "runtime_install.py"

TARGET_NAME = "scorer-python"
PREVIOUS_NAME = "scorer-python.previous"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module(path: Path, name: str):
    assert path.is_file(), (
        f"Missing runtime helper: {path.relative_to(ROOT)}. Issue #2 requires an "
        "importable, CLI-runnable install wrapper."
    )
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def runtime_install():
    return _load_module(RUNTIME_INSTALL, "runtime_install")


def _make_tree(path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        child = path / rel
        child.parent.mkdir(parents=True, exist_ok=True)
        child.write_text(text, encoding="utf-8")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _snapshot(path: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for child in sorted(path.rglob("*")):
        rel = child.relative_to(path).as_posix()
        if child.is_dir():
            snapshot[rel + "/"] = "dir"
        else:
            snapshot[rel] = hashlib.sha256(child.read_bytes()).hexdigest()
    return snapshot


def _run_install(*args: str, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, str(RUNTIME_INSTALL), *args],
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Dry-run planning
# ---------------------------------------------------------------------------


def test_install_dry_run_no_changes(tmp_path: Path) -> None:
    """The default (no ``--yes``) invocation must plan without touching disk."""

    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 2\n"})
    before_target = _snapshot(target)
    before_staged = _snapshot(staged)

    result = _run_install("--target", str(target), "--staged", str(staged))

    assert result.returncode == 0, (
        f"dry-run install must succeed; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    assert "rotated" in output, (
        f"dry-run must report the status the real install would produce; got {output!r}"
    )
    assert "dry-run" in output, (
        f"dry-run must state that nothing was changed; got {output!r}"
    )
    assert _snapshot(target) == before_target, "dry-run modified the active install"
    assert _snapshot(staged) == before_staged, "dry-run modified the staged tree"
    assert not (tmp_path / PREVIOUS_NAME).exists(), "dry-run created a backup"


def test_install_dry_run_plans_pip_without_running_it(tmp_path: Path) -> None:
    """The pip branch must only be *planned* until the operator passes --yes."""

    target = tmp_path / TARGET_NAME

    result = _run_install("--target", str(target), "--package", "jobtrail-ai-scorer")

    assert result.returncode == 0, (
        f"planning the pip branch must succeed; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    assert "pip" in output and "install" in output and "--target" in output, (
        f"the planned pip command must be printed for review; got {output!r}"
    )
    assert not target.exists(), (
        "planning must not run pip and must not create the runtime target"
    )


# ---------------------------------------------------------------------------
# Applied installs
# ---------------------------------------------------------------------------


def test_install_rotates_single_previous_with_yes(tmp_path: Path) -> None:
    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 2\n"})
    old_target = _snapshot(target)

    result = _run_install("--target", str(target), "--staged", str(staged), "--yes")

    assert result.returncode == 0, (
        f"install failed; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "rotated" in (result.stdout + result.stderr)
    assert (target / "pkg" / "__init__.py").read_text(encoding="utf-8") == "x = 2\n"
    assert _snapshot(tmp_path / PREVIOUS_NAME) == old_target
    assert sorted(p.name for p in tmp_path.iterdir()) == [TARGET_NAME, PREVIOUS_NAME], (
        "the install must retain exactly one backup and no dated directories"
    )


def test_install_identical_content_does_not_rotate(tmp_path: Path) -> None:
    """Acceptance criterion: byte-identical reinstalls do not rotate backups."""

    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 1\n"})
    before_target = _snapshot(target)

    result = _run_install("--target", str(target), "--staged", str(staged), "--yes")

    assert result.returncode == 0, (
        f"install failed; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "identical" in (result.stdout + result.stderr)
    assert _snapshot(target) == before_target
    assert not (tmp_path / PREVIOUS_NAME).exists(), (
        "an identical reinstall must not create a backup"
    )


def test_install_keeps_byte_identical_previous(tmp_path: Path) -> None:
    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    previous = _make_tree(tmp_path / PREVIOUS_NAME, {"pkg/__init__.py": "x = 1\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 4\n"})
    before_previous = _snapshot(previous)

    result = _run_install("--target", str(target), "--staged", str(staged), "--yes")

    assert result.returncode == 0, (
        f"install failed; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "kept_previous" in (result.stdout + result.stderr)
    assert _snapshot(previous) == before_previous, (
        "the install must refuse to overwrite a byte-identical backup"
    )
    assert (target / "pkg" / "__init__.py").read_text(encoding="utf-8") == "x = 4\n"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_install_refuses_relative_target(tmp_path: Path) -> None:
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 1\n"})

    result = _run_install(
        "--target", TARGET_NAME, "--staged", str(staged), "--yes", cwd=tmp_path
    )

    assert result.returncode != 0, (
        "a relative --target must be refused so a stray cwd cannot be overwritten; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not (tmp_path / TARGET_NAME).exists()


def test_install_refuses_missing_staged_tree(tmp_path: Path) -> None:
    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    before = _snapshot(target)

    result = _run_install(
        "--target", str(target), "--staged", str(tmp_path / "absent"), "--yes"
    )

    assert result.returncode != 0, (
        f"a missing staged tree must be refused; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert _snapshot(target) == before
    assert not (tmp_path / PREVIOUS_NAME).exists()


def test_install_refuses_both_staged_and_package(tmp_path: Path) -> None:
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 1\n"})

    result = _run_install(
        "--target",
        str(tmp_path / TARGET_NAME),
        "--staged",
        str(staged),
        "--package",
        "jobtrail-ai-scorer",
    )

    assert result.returncode != 0, (
        "--staged and --package are mutually exclusive so the source of the "
        f"install is unambiguous; stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_install_module_exposes_plan_helper(runtime_install, tmp_path: Path) -> None:
    """``plan`` must be importable so schedulers can inspect the decision."""

    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 1\n"})

    assert runtime_install.plan(target, staged) == "identical"
    assert _snapshot(target) == {
        "pkg/": "dir",
        "pkg/__init__.py": hashlib.sha256(b"x = 1\n").hexdigest(),
    }
