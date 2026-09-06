#!/usr/bin/env python3
"""Idempotent retention helper for the runtime ``scorer-python`` install.

The runtime historically renamed the active install into a dated backup on every
reinstall (``scorer-python.backup-<label>-YYYYMMDDTHHMMSSZ``). Those backups grew
without any retention policy, even when the freshly installed tree was
byte-identical to the running one, and ``pip install --target`` could still read
from the stale copies that were left behind.

This module implements the bounded policy required by Issue #2:

* exactly **one** backup is retained, as the sibling directory
  ``<target>.previous`` (never nested inside the active install, so
  ``pip install --target`` never sees it);
* nothing rotates when the staged tree is byte-identical to the active tree
  (status ``identical``);
* a ``.previous`` whose bytes already match the tree being rotated is **never**
  overwritten; the retained backup is kept as-is and the status reports
  ``kept_previous``;
* only content changes rotate the active tree into ``.previous`` (status
  ``rotated``).

Safety properties:

* The module is importable from any working directory (it never depends on the
  caller's ``cwd``) and never touches the network.
* ``rotate`` refuses to install a tree over itself, refuses a source nested in
  the target (or a target nested in the source), refuses symlinked trees, and
  refuses to treat the ``.previous`` backup as an install target.
* The only directory ever removed is a tree whose bytes are provably retained
  elsewhere (a superseded ``.previous`` under the single-backup policy, or an
  active tree whose exact bytes are already held by ``.previous``). Nothing else
  is ever deleted, and there is no globbing and no shell-out.
* ``dry_run=True`` reports the status the real rotation would produce without
  touching the filesystem.

Usage::

    python3 scripts/runtime_backup.py \\
        --target <runtime-root>/scorer-python \\
        --current /tmp/scorer-python.staged \\
        --dry-run

Drop ``--dry-run`` to apply the rotation. Prefer ``scripts/runtime_install.py``
for the full ``pip install --target`` flow.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path


#: Suffix of the single retained backup directory.
PREVIOUS_SUFFIX = ".previous"

#: The staged tree is byte-identical to the active install: nothing changed.
STATUS_IDENTICAL = "identical"
#: The active install was rotated into ``<target>.previous``.
STATUS_ROTATED = "rotated"
#: ``<target>.previous`` already held the same bytes, so it was kept as-is.
STATUS_KEPT_PREVIOUS = "kept_previous"

#: The closed set of statuses ``rotate`` can return.
STATUSES = (STATUS_IDENTICAL, STATUS_ROTATED, STATUS_KEPT_PREVIOUS)

#: Directory names excluded from the content digest. Bytecode caches are
#: regenerated at import time and must not make an identical install look
#: different.
EXCLUDED_NAMES = ("__pycache__",)
#: File suffixes excluded from the content digest.
EXCLUDED_SUFFIXES = (".pyc", ".pyo")

_CHUNK_SIZE = 1024 * 1024


class RuntimeBackupError(RuntimeError):
    """Raised when a rotation would be unsafe; nothing is modified."""


def previous_path(target_dir: Path | str) -> Path:
    """Return the single retained backup path for ``target_dir``.

    The backup is always a *sibling* of the target so the active install stays a
    clean ``pip install --target`` destination.
    """

    target = Path(target_dir)
    return target.with_name(target.name + PREVIOUS_SUFFIX)


def _iter_entries(root: Path) -> list[tuple[str, Path]]:
    """Return ``(relative posix path, path)`` pairs sorted for determinism."""

    entries: list[tuple[str, Path]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        for child in current.iterdir():
            if child.name in EXCLUDED_NAMES:
                continue
            if child.suffix in EXCLUDED_SUFFIXES:
                continue
            entries.append((child.relative_to(root).as_posix(), child))
            if child.is_dir() and not child.is_symlink():
                stack.append(child)
    entries.sort(key=lambda item: item[0])
    return entries


def tree_digest(path: Path | str) -> str:
    """Return a deterministic sha256 digest of a directory tree's content.

    The digest covers relative paths, entry kinds, symlink targets, and file
    bytes. Bytecode caches (``__pycache__``, ``*.pyc``, ``*.pyo``) are excluded
    so a reinstall of identical sources is still recognised as identical.
    """

    root = Path(path)
    if not root.is_dir() or root.is_symlink():
        raise RuntimeBackupError(f"not a directory tree: {root}")

    digest = hashlib.sha256()
    for rel, entry in _iter_entries(root):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        if entry.is_symlink():
            digest.update(b"L\0")
            digest.update(os.readlink(entry).encode("utf-8"))
        elif entry.is_dir():
            digest.update(b"D\0")
        else:
            digest.update(b"F\0")
            with entry.open("rb") as handle:
                while True:
                    chunk = handle.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _is_within(candidate: Path, parent: Path) -> bool:
    return candidate == parent or parent in candidate.parents


def _validate(target: Path, current: Path) -> None:
    """Refuse every unsafe rotation before any filesystem change happens."""

    if target.name.endswith(PREVIOUS_SUFFIX):
        raise RuntimeBackupError(
            f"refusing to install into {target}: the {PREVIOUS_SUFFIX!r} directory "
            "is the retained backup, never an install target."
        )
    if not current.exists():
        raise RuntimeBackupError(f"staged tree does not exist: {current}")
    if current.is_symlink():
        raise RuntimeBackupError(f"refusing to rotate {current}: it is a symlink.")
    if not current.is_dir():
        raise RuntimeBackupError(f"staged tree is not a directory: {current}")
    if target.is_symlink():
        raise RuntimeBackupError(
            f"refusing to replace {target}: the active install is a symlink."
        )
    if target.exists() and not target.is_dir():
        raise RuntimeBackupError(
            f"refusing to replace {target}: it exists and is not a directory."
        )
    if current == target:
        raise RuntimeBackupError(
            f"refusing to overwrite the active install with itself: {target}"
        )
    if _is_within(current, target):
        raise RuntimeBackupError(
            f"refusing to rotate {current}: it lives inside the active install "
            f"{target}."
        )
    if _is_within(target, current):
        raise RuntimeBackupError(
            f"refusing to rotate {current}: the active install {target} lives "
            "inside the staged tree."
        )
    if target.parent != current.parent and not target.parent.is_dir():
        raise RuntimeBackupError(
            f"refusing to install into {target}: parent directory does not exist."
        )


def _discard(tree: Path) -> None:
    """Remove a directory tree whose bytes are provably retained elsewhere.

    Callers must have verified the retention invariant. The helper refuses
    symlinks and non-directories so it can never follow a link out of the
    runtime layout.
    """

    if tree.is_symlink() or not tree.is_dir():
        raise RuntimeBackupError(f"refusing to remove {tree}: not a directory tree.")
    shutil.rmtree(tree)


def rotate(
    target_dir: Path | str,
    current_dir: Path | str,
    *,
    dry_run: bool = False,
) -> str:
    """Install ``current_dir`` as ``target_dir`` under the retention policy.

    Args:
        target_dir: the active runtime install directory (for example
            ``<runtime-root>/scorer-python``).
        current_dir: the freshly staged tree that should become the active
            install (for example a ``pip install --target`` staging directory).
        dry_run: when true, report the status the real rotation would produce
            without touching the filesystem.

    Returns:
        One of :data:`STATUS_IDENTICAL`, :data:`STATUS_ROTATED`, or
        :data:`STATUS_KEPT_PREVIOUS`.

    Raises:
        RuntimeBackupError: when the rotation would be unsafe. Nothing is
            modified in that case.
    """

    target = Path(target_dir)
    current = Path(current_dir)
    _validate(target, current)

    previous = previous_path(target)
    current_digest = tree_digest(current)

    if not target.exists():
        # First install: there is nothing to back up.
        if not dry_run:
            shutil.move(os.fspath(current), os.fspath(target))
        return STATUS_ROTATED

    target_digest = tree_digest(target)
    if current_digest == target_digest:
        # Byte-identical reinstall: no rotation, no backup rewrite. The staged
        # tree is left for the caller to clean up; this helper never deletes it.
        return STATUS_IDENTICAL

    # A staged tree already represented by the retained backup needs no
    # rotation. Leave both the active and staged trees untouched.
    if (
        previous.is_dir()
        and not previous.is_symlink()
        and current_digest == tree_digest(previous)
    ):
        return STATUS_IDENTICAL

    keeps_previous = (
        previous.is_dir()
        and not previous.is_symlink()
        and tree_digest(previous) == target_digest
    )
    if dry_run:
        return STATUS_KEPT_PREVIOUS if keeps_previous else STATUS_ROTATED

    if keeps_previous:
        # The retained backup already holds exactly these bytes; refuse to
        # overwrite it and drop the redundant active copy instead.
        _discard(target)
    else:
        if previous.exists():
            if previous.is_symlink() or not previous.is_dir():
                raise RuntimeBackupError(
                    f"refusing to replace {previous}: not a directory tree."
                )
            # Single-backup retention: the superseded backup is discarded.
            _discard(previous)
        shutil.move(os.fspath(target), os.fspath(previous))

    shutil.move(os.fspath(current), os.fspath(target))
    return STATUS_KEPT_PREVIOUS if keeps_previous else STATUS_ROTATED


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="runtime_backup.py",
        description=(
            "Rotate a staged runtime tree into place while retaining exactly one "
            f"{PREVIOUS_SUFFIX!r} backup, and only when the content changed."
        ),
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Absolute path to the active runtime install directory.",
    )
    parser.add_argument(
        "--current",
        required=True,
        help="Absolute path to the staged tree that should become the target.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the status the rotation would produce without changing anything.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        status = rotate(args.target, args.current, dry_run=args.dry_run)
    except RuntimeBackupError as error:
        print(str(error), file=sys.stderr)
        return 2

    prefix = "dry-run: " if args.dry_run else ""
    print(f"{prefix}status={status} target={Path(args.target)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
