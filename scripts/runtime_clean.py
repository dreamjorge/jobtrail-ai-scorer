#!/usr/bin/env python3
"""Remove explicitly named, obsolete scorer runtime backups.

This command is deliberately narrow: it never discovers files.  Every target
must be an absolute directory named ``scorer-python.previous`` or a dated
``scorer-python.backup-*`` directory, and ``--yes`` is required to remove it.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PREVIOUS_SUFFIX = ".previous"
_PREVIOUS_NAME = "scorer-python.previous"
_DATED_BACKUP = re.compile(
    r"^scorer-python\.backup-(?:[^/]+-)?\d{8}T\d{6}Z$"
)


class RuntimeCleanError(RuntimeError):
    """Raised when a cleanup request is outside the narrow safe scope."""


def _allowed_name(path: Path) -> bool:
    return path.name == _PREVIOUS_NAME or bool(_DATED_BACKUP.fullmatch(path.name))


def _validate_target(path: Path, runtime_root: Path | None, missing_ok: bool) -> None:
    if not path.is_absolute():
        raise RuntimeCleanError(f"refusing relative target: {path}")
    if runtime_root is not None:
        try:
            path.parent.resolve().relative_to(runtime_root.resolve())
        except ValueError as exc:
            raise RuntimeCleanError(
                f"refusing target outside runtime root {runtime_root}: {path}"
            ) from exc
        if path.parent.resolve() != runtime_root.resolve():
            raise RuntimeCleanError(
                f"refusing target not directly under runtime root {runtime_root}: {path}"
            )
    if not _allowed_name(path):
        raise RuntimeCleanError(f"refusing unrelated cleanup target: {path}")
    if not _exists(path):
        if missing_ok:
            return
        raise RuntimeCleanError(f"cleanup target does not exist: {path}")
    if path.is_symlink():
        raise RuntimeCleanError(f"refusing symlink cleanup target: {path}")
    if not path.is_dir():
        raise RuntimeCleanError(f"refusing non-directory cleanup target: {path}")


def _exists(path: Path) -> bool:
    """Check existence without following a final symlink."""

    return path.exists() or path.is_symlink()


def clean(
    targets: list[Path | str],
    *,
    yes: bool = False,
    missing_ok: bool = False,
    runtime_root: Path | str | None = None,
) -> list[Path]:
    """Validate and optionally remove the explicit backup ``targets``."""

    paths = [Path(target) for target in targets]
    root = None if runtime_root is None else Path(runtime_root)
    if root is not None and not root.is_absolute():
        raise RuntimeCleanError(f"refusing relative runtime root: {root}")
    if not paths:
        raise RuntimeCleanError("pass at least one --target")
    for path in paths:
        _validate_target(path, root, missing_ok)
    if not yes:
        raise RuntimeCleanError(
            "refusing to remove targets without --yes: "
            + ", ".join(str(path) for path in paths)
        )
    removed: list[Path] = []
    for path in paths:
        if not _exists(path):
            continue
        # All targets were validated before this loop, so one bad target cannot
        # cause an earlier valid target to be removed.
        shutil.rmtree(path)
        removed.append(path)
    return removed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove explicitly listed scorer-python backup directories."
    )
    parser.add_argument(
        "--runtime-root",
        help="Optional absolute root; targets must be direct children of it.",
    )
    parser.add_argument("--target", action="append", required=True)
    parser.add_argument("--yes", action="store_true", help="Confirm removal.")
    parser.add_argument(
        "--missing-ok",
        action="store_true",
        help="Treat already absent targets as success.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        removed = clean(
            args.target,
            yes=args.yes,
            missing_ok=args.missing_ok,
            runtime_root=args.runtime_root,
        )
    except RuntimeCleanError as error:
        print(str(error), file=sys.stderr)
        return 2
    for path in removed:
        print(f"removed {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
