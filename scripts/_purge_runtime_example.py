#!/usr/bin/env python3
"""Strict opt-in helper to remove the duplicated runtime scorer config example.

Historically, a duplicate of ``config.example.yaml`` lived at the operator
runtime root as ``scorer.config.example.yaml`` and contained an embedded
private IP. The repository now ships a single canonical example at
``scripts/scorer-config.example.yaml``. This helper removes that specific
runtime duplicate after the operator confirms with ``--yes``.

Safety properties:

* Refuses to act without ``--yes`` (the script never deletes anything by
  accident).
* Refuses any target whose basename is not exactly
  ``scorer.config.example.yaml`` (so a typo cannot delete a CV or config).
* Requires an absolute path (relative paths cannot accidentally point at
  ``config.yaml`` in the current working directory).
* Refuses symlinks, directories, and missing files (the script never follows
  links or walks directories).
* Uses ``os.remove`` on the explicit file only — no shell-out, no recursive
  delete, no globbing.

Usage::

    python3 scripts/_purge_runtime_example.py \\
        --runtime-example <runtime-root>/jobtrail/scorer.config.example.yaml \\
        --yes

Run without ``--yes`` to see the refusal without touching the filesystem.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


#: The exact basename the helper is allowed to remove. Any other target is
#: refused so a typo cannot point the helper at a CV, profile, or unrelated
#: config file.
ALLOWED_BASENAME = "scorer.config.example.yaml"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="_purge_runtime_example.py",
        description=(
            "Strict opt-in helper to remove the duplicated runtime scorer "
            "config example whose basename is "
            f"{ALLOWED_BASENAME!r}."
        ),
    )
    parser.add_argument(
        "--runtime-example",
        required=True,
        help=(
            "Absolute path to the runtime example to remove. Must point at a "
            f"regular file whose basename is exactly {ALLOWED_BASENAME!r}; "
            "nothing else is accepted."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm removal of the explicit --runtime-example path.",
    )
    return parser.parse_args(argv)


def _validate_target(raw_arg: str, resolved: Path) -> str | None:
    """Return an error message if the target is unsafe, else None.

    The checks are intentionally layered: the basename gate prevents a typo
    from pointing the helper at a CV or config, the absolute-path gate
    prevents a relative ``config.yaml`` from being removed by accident (we
    inspect the raw argument, not the resolved path, so ``./<basename>`` is
    still refused), and the symlink / directory gates prevent the helper
    from following links or walking parent directories.
    """

    if resolved.name != ALLOWED_BASENAME:
        return (
            f"refusing to remove {resolved}: basename must be "
            f"{ALLOWED_BASENAME!r} so a typo cannot delete a CV, profile, or "
            "unrelated config."
        )
    if not Path(raw_arg).is_absolute():
        return (
            f"refusing to remove {resolved}: --runtime-example must be an "
            "absolute path so a relative argument cannot accidentally target "
            "a local config.yaml."
        )
    # Check the raw, unresolved argument for a symlink: `resolved` already
    # had `.resolve()` follow any link down to its real target, so checking
    # `resolved.is_symlink()` here would always be False and never catch a
    # symlink planted at the given path.
    if Path(raw_arg).is_symlink():
        return f"refusing to remove {raw_arg}: refusing to follow symlinks."
    if not resolved.exists():
        return f"runtime example does not exist: {resolved}"
    if not resolved.is_file():
        return f"refusing to remove {resolved}: not a regular file."
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    resolved = Path(args.runtime_example).resolve(strict=False)

    error = _validate_target(args.runtime_example, resolved)
    if error is not None:
        print(error, file=sys.stderr)
        return 2

    if not args.yes:
        print(
            f"refusing to remove {resolved} without --yes; pass --yes to confirm.",
            file=sys.stderr,
        )
        return 2

    # Single-file, explicit removal. No directory walking, no globbing, no
    # shell-out. The target has already been validated above.
    os.remove(resolved)
    print(f"removed runtime example: {resolved}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
