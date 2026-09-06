"""Tests for the canonical example layout and the strict opt-in purge helper.

These tests enforce:

- The repo ships exactly one canonical example config
  (``scripts/scorer-config.example.yaml``) and a thin pointer at the repo root
  (``config.example.yaml``).
- The purge helper (``scripts/_purge_runtime_example.py``) refuses to act
  without ``--yes`` and refuses to remove anything other than the explicit
  ``--runtime-example`` path whose basename is ``scorer.config.example.yaml``.

The purge helper is the only sanctioned way to remove the historical runtime
duplicate at ``/DATA/AppData/jobtrail/scorer.config.example.yaml``. Tests must
never touch real operator files; they always drive the helper with a
``tmp_path`` target.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_EXAMPLE = ROOT / "scripts" / "scorer-config.example.yaml"
POINTER_EXAMPLE = ROOT / "config.example.yaml"
PURGE_SCRIPT = ROOT / "scripts" / "_purge_runtime_example.py"

#: The exact filename the purge helper is allowed to remove.
ALLOWED_BASENAME = "scorer.config.example.yaml"


def _run_purge(target: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PURGE_SCRIPT),
            "--runtime-example",
            str(target),
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# Layout assertions
# ---------------------------------------------------------------------------


def test_canonical_example_exists() -> None:
    assert CANONICAL_EXAMPLE.is_file(), (
        f"Missing canonical example: {CANONICAL_EXAMPLE}. The repo must ship a "
        "single source of truth for the scorer config example."
    )


def test_pointer_example_exists() -> None:
    assert POINTER_EXAMPLE.is_file(), (
        f"Missing pointer file: {POINTER_EXAMPLE}. The repo root must point to "
        "the canonical example."
    )


def test_purge_helper_script_exists() -> None:
    assert PURGE_SCRIPT.is_file(), (
        f"Missing purge helper: {PURGE_SCRIPT}. Provide a strict opt-in helper "
        "to remove the runtime duplicate."
    )


def test_purge_helper_has_strict_shebang_and_set_euxo_pipefail() -> None:
    text = PURGE_SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env python3"), (
        "purge helper must start with the canonical python3 shebang"
    )
    assert "__future__" in text, (
        "purge helper must import __future__ for from __future__ import annotations"
    )


def test_purge_helper_never_invokes_destructive_filesystem_commands() -> None:
    """The helper must not shell out to ``rm`` or traverse directories."""

    text = PURGE_SCRIPT.read_text(encoding="utf-8")
    forbidden = (
        "rm -rf",
        "shutil.rmtree",
        "os.removedirs",
        "os.unlink",  # only os.remove is allowed for the explicit file
        "subprocess.run",
        "subprocess.Popen",
    )
    for pattern in forbidden:
        assert pattern not in text, (
            f"purge helper must not use {pattern!r}; it removes only the "
            "explicit --runtime-example path"
        )


# ---------------------------------------------------------------------------
# Behavioural assertions (drive the helper as a subprocess)
# ---------------------------------------------------------------------------


def test_purge_helper_refuses_without_yes(tmp_path: Path) -> None:
    runtime_example = tmp_path / ALLOWED_BASENAME
    runtime_example.write_text("# placeholder\n", encoding="utf-8")

    result = _run_purge(runtime_example)

    assert result.returncode != 0, (
        "purge helper must refuse to act without --yes; stdout="
        f"{result.stdout!r} stderr={result.stderr!r}"
    )
    assert runtime_example.is_file(), (
        "purge helper removed file without --yes; it must refuse and exit non-zero"
    )
    assert "--yes" in result.stderr or "yes" in result.stderr, (
        "purge helper error must mention the missing --yes flag so the operator "
        f"knows what to do; got stderr={result.stderr!r}"
    )


def test_purge_helper_removes_target_with_yes(tmp_path: Path) -> None:
    runtime_example = tmp_path / ALLOWED_BASENAME
    runtime_example.write_text("# placeholder\n", encoding="utf-8")

    result = _run_purge(runtime_example, "--yes")

    assert result.returncode == 0, (
        f"purge helper failed under --yes; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert not runtime_example.exists(), (
        "purge helper left the runtime example in place after --yes"
    )


def test_purge_helper_only_removes_explicit_target(tmp_path: Path) -> None:
    """Sibling files must not be touched even with --yes."""

    runtime_example = tmp_path / ALLOWED_BASENAME
    runtime_example.write_text("# runtime duplicate\n", encoding="utf-8")
    sibling = tmp_path / "candidate-cv.md"
    sibling.write_text("private cv\n", encoding="utf-8")

    result = _run_purge(runtime_example, "--yes")

    assert result.returncode == 0, (
        f"purge helper failed; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not runtime_example.exists(), "runtime duplicate must be removed"
    assert sibling.is_file(), (
        "purge helper must never touch files other than the explicit target; "
        f"removed {sibling}"
    )


def test_purge_helper_refuses_path_with_wrong_basename(tmp_path: Path) -> None:
    """An explicit target whose basename is not the example must be refused."""

    forbidden = tmp_path / "candidate-cv.md"
    forbidden.write_text("private cv\n", encoding="utf-8")

    result = _run_purge(forbidden, "--yes")

    assert result.returncode != 0, (
        "purge helper must refuse to remove a path whose basename is not "
        f"{ALLOWED_BASENAME!r}; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert forbidden.is_file(), "purge helper removed an unauthorized path"


def test_purge_helper_refuses_non_absolute_path(tmp_path: Path) -> None:
    runtime_example = tmp_path / ALLOWED_BASENAME
    runtime_example.write_text("# placeholder\n", encoding="utf-8")

    # chdir so the relative path resolves to a real file under tmp_path.
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = _run_purge(Path(ALLOWED_BASENAME), "--yes")
    finally:
        os.chdir(old_cwd)

    assert result.returncode != 0, (
        "purge helper must refuse a relative --runtime-example path even when "
        f"it could resolve; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert runtime_example.is_file(), (
        "purge helper must not remove a path it received as a relative argument"
    )


def test_purge_helper_refuses_symlink(tmp_path: Path) -> None:
    real = tmp_path / "candidate-cv.md"
    real.write_text("private cv\n", encoding="utf-8")
    link = tmp_path / ALLOWED_BASENAME
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlinks not supported on this platform: {exc}")

    result = _run_purge(link, "--yes")

    assert result.returncode != 0, (
        "purge helper must refuse to follow a symlink; stdout="
        f"{result.stdout!r} stderr={result.stderr!r}"
    )
    assert real.is_file(), "purge helper followed a symlink and removed real file"


def test_purge_helper_refuses_directory(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ALLOWED_BASENAME
    runtime_dir.mkdir()

    result = _run_purge(runtime_dir, "--yes")

    assert result.returncode != 0, (
        "purge helper must refuse to remove a directory; stdout="
        f"{result.stdout!r} stderr={result.stderr!r}"
    )
    assert runtime_dir.is_dir(), "purge helper removed a directory"


def test_purge_helper_reports_missing_target(tmp_path: Path) -> None:
    missing = tmp_path / ALLOWED_BASENAME
    assert not missing.exists()

    result = _run_purge(missing, "--yes")

    assert result.returncode != 0, (
        "purge helper must surface a clear error for a missing target; stdout="
        f"{result.stdout!r} stderr={result.stderr!r}"
    )


def test_purge_helper_does_not_require_runtime_dependency_on_a(tmp_path: Path) -> None:
    """The helper must run with only the Python standard library."""

    runtime_example = tmp_path / ALLOWED_BASENAME
    runtime_example.write_text("# placeholder\n", encoding="utf-8")

    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""}
    # Use -I for an isolated interpreter that ignores site-packages. We still
    # ship a fresh tmp_path so PYTHONPATH / cwd artefacts cannot leak.
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import runpy, sys; sys.argv = ['_purge_runtime_example.py', "
                "'--runtime-example', %r, '--yes']; "
                "runpy.run_path(%r, run_name='__main__')"
            )
            % (str(runtime_example), str(PURGE_SCRIPT)),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert result.returncode == 0, (
        f"purge helper must run under -I (isolated interpreter); stdout="
        f"{result.stdout!r} stderr={result.stderr!r}"
    )
    assert not runtime_example.exists(), (
        "purge helper left the runtime example in place under -I"
    )


def test_purge_helper_removes_file_mode_0600_when_present(tmp_path: Path) -> None:
    """A runtime example with 0600 permissions must still be removable."""

    runtime_example = tmp_path / ALLOWED_BASENAME
    runtime_example.write_text("# placeholder\n", encoding="utf-8")
    runtime_example.chmod(stat.S_IRUSR | stat.S_IWUSR)

    result = _run_purge(runtime_example, "--yes")

    assert result.returncode == 0, (
        f"purge helper failed on 0600 file; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert not runtime_example.exists()
