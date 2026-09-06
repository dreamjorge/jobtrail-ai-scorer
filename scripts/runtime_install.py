#!/usr/bin/env python3
"""Operator-run installer for the runtime ``scorer-python`` tree (Issue #2).

This wrapper replaces the historical "rename the active install into a dated
backup on every reinstall" pattern. It stages the new content, then hands the
staged tree to :func:`runtime_backup.rotate`, which retains exactly one
``<target>.previous`` backup and only when the content actually changed.

Two sources of content are supported, and exactly one must be chosen:

* ``--staged <dir>``: use a tree that is already built (no network, no ``pip``).
* ``--package <spec>`` (repeatable): build the tree with
  ``pip install --target <staging> <spec>...``. This branch is **operator-run
  only**; it is never invoked by CI or by the test suite.

Safety properties:

* Nothing is modified unless the operator passes ``--yes``. Without it the
  command is a dry run that prints the planned status (and the planned ``pip``
  command line) and exits ``0``.
* ``--target`` must be an absolute path, so a stray working directory can never
  be replaced.
* ``--staged`` and ``--package`` are mutually exclusive, so the source of an
  install is never ambiguous.
* The retention decision, every refusal, and the only deletions live in
  ``scripts/runtime_backup.py``; this module never removes anything itself.
* The module is importable from any working directory (``plan`` and ``install``
  are reusable from a scheduler).

Usage::

    # 1. Review the plan (default: dry run, changes nothing).
    python3 scripts/runtime_install.py \\
        --target <runtime-root>/scorer-python \\
        --staged /tmp/scorer-python.staged

    # 2. Apply it.
    python3 scripts/runtime_install.py \\
        --target <runtime-root>/scorer-python \\
        --staged /tmp/scorer-python.staged \\
        --yes

Statuses reported on stderr: ``identical`` (byte-identical reinstall, nothing
rotated), ``rotated`` (content changed, single backup refreshed), and
``kept_previous`` (the retained backup already held these bytes, so it was not
overwritten).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess  # noqa: S404 - only used for the opt-in `pip install --target`
import sys
import tempfile
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    # Keep the sibling helper importable no matter which directory the operator
    # (or scheduler) runs this script from.
    sys.path.insert(0, str(_SCRIPTS_DIR))

import runtime_backup  # noqa: E402 - sibling helper; sys.path fixed just above

RuntimeBackupError = runtime_backup.RuntimeBackupError


def plan(target_dir: Path | str, staged_dir: Path | str) -> str:
    """Return the status the real install would produce, changing nothing."""

    return runtime_backup.rotate(target_dir, staged_dir, dry_run=True)


def install(target_dir: Path | str, staged_dir: Path | str) -> str:
    """Apply the retention policy and return the resulting status."""

    return runtime_backup.rotate(target_dir, staged_dir, dry_run=False)


def pip_command(staging_dir: Path | str, packages: list[str]) -> list[str]:
    """Return the ``pip install --target`` command line for ``packages``."""

    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        os.fspath(staging_dir),
        *packages,
    ]


def build_staged_tree(staging_dir: Path | str, packages: list[str]) -> Path:
    """Populate ``staging_dir`` with ``pip install --target``.

    Operator-run only: this is the single place in the module that reaches the
    network. Tests and CI always pass ``--staged`` instead.
    """

    command = pip_command(staging_dir, packages)
    print(f"running: {' '.join(command)}", file=sys.stderr)
    subprocess.run(command, check=True)  # noqa: S603 - fixed argv, no shell
    return Path(staging_dir)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="runtime_install.py",
        description=(
            "Install a runtime tree idempotently, retaining exactly one "
            "<target>.previous backup and only when the content changed."
        ),
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Absolute path to the active runtime install directory.",
    )
    parser.add_argument(
        "--staged",
        help=(
            "Absolute path to an already-built tree to install. Mutually "
            "exclusive with --package."
        ),
    )
    parser.add_argument(
        "--package",
        action="append",
        default=[],
        metavar="SPEC",
        help=(
            "Package spec to stage with `pip install --target` (repeatable). "
            "Operator-run only; mutually exclusive with --staged."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply the install. Without it the command is a dry run.",
    )
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> str | None:
    """Return an error message when the invocation is unsafe, else ``None``."""

    if not Path(args.target).is_absolute():
        return (
            "refusing to install: --target must be an absolute path so a "
            "relative argument cannot replace a directory in the current "
            "working directory."
        )
    if args.staged and args.package:
        return (
            "refusing to install: --staged and --package are mutually exclusive; "
            "choose exactly one source for the install."
        )
    if not args.staged and not args.package:
        return "refusing to install: pass either --staged <dir> or --package <spec>."
    if args.staged and not Path(args.staged).is_absolute():
        return "refusing to install: --staged must be an absolute path."
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    error = _validate_args(args)
    if error is not None:
        print(error, file=sys.stderr)
        return 2

    target = Path(args.target)

    if args.staged:
        staged = Path(args.staged)
        try:
            status = (
                install(target, staged) if args.yes else plan(target, staged)
            )
        except RuntimeBackupError as backup_error:
            print(str(backup_error), file=sys.stderr)
            return 2
        prefix = "" if args.yes else "dry-run: "
        print(f"{prefix}status={status} target={target}", file=sys.stderr)
        return 0

    # --package branch: staging needs pip, so plan it before running anything.
    if not args.yes:
        planned = pip_command("<staging-dir>", args.package)
        print(
            "dry-run: would stage with: " + " ".join(planned),
            file=sys.stderr,
        )
        print(
            f"dry-run: would then apply the retention policy to {target} "
            "(pass --yes to run it)",
            file=sys.stderr,
        )
        return 0

    staging_parent = tempfile.mkdtemp(prefix="scorer-python.staging.")
    staging = Path(staging_parent) / "tree"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        build_staged_tree(staging, args.package)
        status = install(target, staging)
    except subprocess.CalledProcessError as pip_error:
        print(f"pip staging failed with exit code {pip_error.returncode}", file=sys.stderr)
        return 2
    except RuntimeBackupError as backup_error:
        print(str(backup_error), file=sys.stderr)
        return 2
    finally:
        # Only the private staging directory this run created is cleaned up.
        shutil.rmtree(staging_parent, ignore_errors=True)

    print(f"status={status} target={target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
