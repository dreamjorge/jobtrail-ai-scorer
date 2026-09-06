"""Tests for the idempotent runtime backup retention helper (Issue #2).

The runtime historically renamed the active ``scorer-python`` install into a
dated backup on *every* reinstall
(``scorer-python.backup-<label>-YYYYMMDDTHHMMSSZ``), so backups accumulated
even when the freshly installed tree was byte-identical to the running one.

``scripts/runtime_backup.py`` replaces that pattern with a bounded, idempotent
retention policy:

* exactly one backup is retained, as a sibling directory named
  ``<target>.previous``;
* nothing rotates when the staged tree is byte-identical to the active tree;
* a byte-identical ``.previous`` is never overwritten (the retained backup is
  kept as-is and the status reports ``kept_previous``);
* the helper refuses to overwrite the active install with itself, and refuses
  to treat the ``.previous`` backup as an install target.

Every test drives temporary directories under ``tmp_path``. The tests never
read, write, or delete operator runtime files, never touch ``pip``, and never
reach the network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_BACKUP = ROOT / "scripts" / "runtime_backup.py"

TARGET_NAME = "scorer-python"
PREVIOUS_NAME = "scorer-python.previous"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module(path: Path, name: str):
    """Import a helper script by path so tests exercise the shipped file."""

    assert path.is_file(), (
        f"Missing runtime helper: {path.relative_to(ROOT)}. Issue #2 requires "
        "an importable, CLI-runnable retention helper."
    )
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def runtime_backup():
    return _load_module(RUNTIME_BACKUP, "runtime_backup")


def _make_tree(path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        child = path / rel
        child.parent.mkdir(parents=True, exist_ok=True)
        child.write_text(text, encoding="utf-8")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _snapshot(path: Path) -> dict[str, str]:
    """Return ``{relative posix path: sha256 of bytes}`` for a directory tree."""

    snapshot: dict[str, str] = {}
    for child in sorted(path.rglob("*")):
        rel = child.relative_to(path).as_posix()
        if child.is_dir():
            snapshot[rel + "/"] = "dir"
        else:
            snapshot[rel] = hashlib.sha256(child.read_bytes()).hexdigest()
    return snapshot


# ---------------------------------------------------------------------------
# Digest behaviour
# ---------------------------------------------------------------------------


def test_tree_digest_is_stable_for_identical_content(
    runtime_backup, tmp_path: Path
) -> None:
    left = _make_tree(tmp_path / "left", {"pkg/__init__.py": "x = 1\n"})
    right = _make_tree(tmp_path / "right", {"pkg/__init__.py": "x = 1\n"})

    assert runtime_backup.tree_digest(left) == runtime_backup.tree_digest(right)


def test_tree_digest_changes_with_content(runtime_backup, tmp_path: Path) -> None:
    left = _make_tree(tmp_path / "left", {"pkg/__init__.py": "x = 1\n"})
    right = _make_tree(tmp_path / "right", {"pkg/__init__.py": "x = 2\n"})

    assert runtime_backup.tree_digest(left) != runtime_backup.tree_digest(right)


def test_tree_digest_ignores_bytecode_caches(runtime_backup, tmp_path: Path) -> None:
    """Compiled bytecode must not make an identical install look different."""

    left = _make_tree(tmp_path / "left", {"pkg/__init__.py": "x = 1\n"})
    right = _make_tree(
        tmp_path / "right",
        {
            "pkg/__init__.py": "x = 1\n",
            "pkg/__pycache__/__init__.cpython-311.pyc": "not-real-bytecode",
        },
    )

    assert runtime_backup.tree_digest(left) == runtime_backup.tree_digest(right), (
        "__pycache__ / *.pyc entries must be excluded from the digest so a "
        "byte-identical reinstall is still detected as identical"
    )


# ---------------------------------------------------------------------------
# rotate() behaviour
# ---------------------------------------------------------------------------


def test_rotate_keeps_previous_when_identical(runtime_backup, tmp_path: Path) -> None:
    """A byte-identical reinstall must not rotate or rewrite any backup."""

    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    previous = _make_tree(tmp_path / PREVIOUS_NAME, {"pkg/__init__.py": "old = 0\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 1\n"})

    before_previous = _snapshot(previous)
    before_target = _snapshot(target)

    status = runtime_backup.rotate(target, staged)

    assert status == runtime_backup.STATUS_IDENTICAL, (
        f"identical content must report {runtime_backup.STATUS_IDENTICAL!r}; "
        f"got {status!r}"
    )
    assert _snapshot(previous) == before_previous, (
        "an identical reinstall must leave the retained backup untouched"
    )
    assert _snapshot(target) == before_target, (
        "an identical reinstall must leave the active install untouched"
    )


def test_rotate_replaces_when_different(runtime_backup, tmp_path: Path) -> None:
    """Changed content rotates the active tree into the single ``.previous``."""

    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    previous = _make_tree(tmp_path / PREVIOUS_NAME, {"pkg/__init__.py": "ancient\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 2\n"})

    old_target = _snapshot(target)

    status = runtime_backup.rotate(target, staged)

    assert status == runtime_backup.STATUS_ROTATED, (
        f"changed content must report {runtime_backup.STATUS_ROTATED!r}; got {status!r}"
    )
    assert (target / "pkg" / "__init__.py").read_text(encoding="utf-8") == "x = 2\n", (
        "the active install must hold the staged content after rotation"
    )
    assert _snapshot(previous) == old_target, (
        "the single retained backup must hold the previously active content"
    )
    assert not staged.exists(), (
        "the staged tree must be moved into place, not copied and left behind"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        TARGET_NAME,
        PREVIOUS_NAME,
    ], "rotation must not create dated backup directories"


def test_rotate_refuses_to_overwrite_identical_previous(
    runtime_backup, tmp_path: Path
) -> None:
    """A ``.previous`` holding the same bytes as the active tree is preserved."""

    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    previous = _make_tree(tmp_path / PREVIOUS_NAME, {"pkg/__init__.py": "x = 1\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 3\n"})

    before_previous = _snapshot(previous)

    status = runtime_backup.rotate(target, staged)

    assert status == runtime_backup.STATUS_KEPT_PREVIOUS, (
        "rotating onto a byte-identical backup must report "
        f"{runtime_backup.STATUS_KEPT_PREVIOUS!r}; got {status!r}"
    )
    assert _snapshot(previous) == before_previous, (
        "the helper must refuse to overwrite a byte-identical backup"
    )
    assert (target / "pkg" / "__init__.py").read_text(encoding="utf-8") == "x = 3\n", (
        "the staged content must still become the active install"
    )


def test_rotate_replaces_target_when_staged_matches_only_previous(
    runtime_backup, tmp_path: Path
) -> None:
    """Staged bytes matching the old ``.previous`` must still rotate.

    The staged tree can legitimately match an *older* ``.previous`` backup
    while the *active* tree already differs (someone rotated forward and is
    now rotating back, or the same content was staged twice). ``rotate``'s
    contract is to make ``current_dir`` become ``target_dir`` regardless;
    reporting ``identical`` here would leave the (different) active tree in
    place and silently drop the staged content.
    """

    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 2\n"})
    _make_tree(tmp_path / PREVIOUS_NAME, {"pkg/__init__.py": "x = 1\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 1\n"})

    status = runtime_backup.rotate(target, staged)

    assert status == runtime_backup.STATUS_ROTATED, (
        "staged content matching only the retained backup (not the active "
        f"tree) must still rotate; got {status!r}"
    )
    assert (target / "pkg" / "__init__.py").read_text(encoding="utf-8") == "x = 1\n", (
        "the staged content must become the active install"
    )


def test_rotate_installs_when_target_missing(runtime_backup, tmp_path: Path) -> None:
    target = tmp_path / TARGET_NAME
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 1\n"})

    status = runtime_backup.rotate(target, staged)

    assert status == runtime_backup.STATUS_ROTATED
    assert (target / "pkg" / "__init__.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (tmp_path / PREVIOUS_NAME).exists(), (
        "a first install has nothing to back up and must not create .previous"
    )


def test_rotate_refuses_to_overwrite_active(runtime_backup, tmp_path: Path) -> None:
    """``current_dir == target_dir`` (or nested) must be refused, not rotated."""

    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    before = _snapshot(target)

    with pytest.raises(runtime_backup.RuntimeBackupError):
        runtime_backup.rotate(target, target)

    nested = _make_tree(target / "staged", {"pkg/__init__.py": "x = 9\n"})
    with pytest.raises(runtime_backup.RuntimeBackupError):
        runtime_backup.rotate(target, nested)

    assert before.items() <= _snapshot(target).items(), (
        "a refused rotation must leave the active install intact"
    )
    assert not (tmp_path / PREVIOUS_NAME).exists(), (
        "a refused rotation must not create a backup"
    )


def test_rotate_refuses_dotdot_disguised_nesting(runtime_backup, tmp_path: Path) -> None:
    """A ``..``-laden path must not bypass the nested-source/target check.

    Comparing raw ``Path.parents`` (without resolving ``..``) would let a
    source path like ``<target>/../<target-name>/staged`` slip past the
    literal containment check while still landing inside the active install
    after normalization, corrupting the final ``shutil.move``.
    """

    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    nested = _make_tree(target / "staged", {"pkg/__init__.py": "x = 9\n"})
    disguised = target.parent / ".." / target.parent.name / TARGET_NAME / "staged"

    with pytest.raises(runtime_backup.RuntimeBackupError):
        runtime_backup.rotate(target, disguised)

    assert nested.exists(), "a refused rotation must leave the staged tree intact"


def test_rotate_refuses_previous_as_target(runtime_backup, tmp_path: Path) -> None:
    previous = _make_tree(tmp_path / PREVIOUS_NAME, {"pkg/__init__.py": "old\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "new\n"})

    with pytest.raises(runtime_backup.RuntimeBackupError):
        runtime_backup.rotate(previous, staged)

    assert (previous / "pkg" / "__init__.py").read_text(encoding="utf-8") == "old\n"


def test_rotate_refuses_missing_or_non_directory_source(
    runtime_backup, tmp_path: Path
) -> None:
    target = tmp_path / TARGET_NAME

    with pytest.raises(runtime_backup.RuntimeBackupError):
        runtime_backup.rotate(target, tmp_path / "does-not-exist")

    regular_file = tmp_path / "wheel.whl"
    regular_file.write_text("not a tree\n", encoding="utf-8")
    with pytest.raises(runtime_backup.RuntimeBackupError):
        runtime_backup.rotate(target, regular_file)

    assert not target.exists()


def test_rotate_dry_run_makes_no_changes(runtime_backup, tmp_path: Path) -> None:
    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 2\n"})
    before = {path.name: _snapshot(path) for path in (target, staged)}

    status = runtime_backup.rotate(target, staged, dry_run=True)

    assert status == runtime_backup.STATUS_ROTATED, (
        "dry-run must report the status the real rotation would produce"
    )
    assert {path.name: _snapshot(path) for path in (target, staged)} == before
    assert not (tmp_path / PREVIOUS_NAME).exists(), (
        "dry-run must not create the retained backup"
    )


def test_previous_path_is_a_sibling_of_the_target(
    runtime_backup, tmp_path: Path
) -> None:
    target = tmp_path / TARGET_NAME

    assert runtime_backup.previous_path(target) == tmp_path / PREVIOUS_NAME, (
        "the retained backup must be a sibling directory so pip install --target "
        "never reads it from inside the active install"
    )


# ---------------------------------------------------------------------------
# Import / CLI safety
# ---------------------------------------------------------------------------


def test_helper_is_importable_from_any_directory(tmp_path: Path) -> None:
    """The helper must not depend on the caller's working directory."""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib.util, sys\n"
                f"spec = importlib.util.spec_from_file_location('rb', {str(RUNTIME_BACKUP)!r})\n"
                "module = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(module)\n"
                "print(module.STATUS_ROTATED)\n"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"helper must import from any cwd; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "rotated" in result.stdout


def test_cli_reports_status_without_mutating_in_dry_run(tmp_path: Path) -> None:
    target = _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    staged = _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 2\n"})
    before = _snapshot(target)

    result = subprocess.run(
        [
            sys.executable,
            str(RUNTIME_BACKUP),
            "--target",
            str(target),
            "--current",
            str(staged),
            "--dry-run",
        ],
        cwd=os.fspath(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"dry-run CLI must succeed; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "rotated" in (result.stdout + result.stderr)
    assert _snapshot(target) == before
    assert staged.is_dir()
    assert not (tmp_path / PREVIOUS_NAME).exists()


def test_cli_refuses_relative_paths(tmp_path: Path) -> None:
    """--target/--current are documented as absolute; a relative path must
    be refused instead of silently depending on the invoker's cwd."""

    _make_tree(tmp_path / TARGET_NAME, {"pkg/__init__.py": "x = 1\n"})
    _make_tree(tmp_path / "staged", {"pkg/__init__.py": "x = 2\n"})

    result = subprocess.run(
        [
            sys.executable,
            str(RUNTIME_BACKUP),
            "--target",
            TARGET_NAME,
            "--current",
            "staged",
            "--dry-run",
        ],
        cwd=os.fspath(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0, "a relative --target/--current must be refused"
    assert "absolute" in result.stderr.lower()
