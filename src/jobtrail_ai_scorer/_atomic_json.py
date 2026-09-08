"""Shared atomic-JSON helpers for persistent local state files.

This module factors the tmp+rename + 0600 + ``fcntl.flock`` machinery that
:class:`jobtrail_ai_scorer.seen_cache.SeenCache` historically carried inline
out of that class so :class:`jobtrail_ai_scorer.circuit_breaker.CircuitBreaker`
(and any future stateful module) can share the same on-disk contract:

* reads tolerate missing or corrupt files by returning the caller's
  ``default`` instead of raising;
* writes are atomic (``tmp + os.replace``) and the on-disk mode is
  tightened to ``0600`` regardless of umask or pre-existing permissions;
* if the caller provides a ``lock_path``, the read/write is wrapped in an
  advisory ``fcntl.flock`` so two concurrent processes cannot interleave
  their file operations. Lock failures degrade to no-lock — storage must
  never crash the calling automation.

The helpers intentionally expose only file-I/O primitives. Higher-level
modules are responsible for validating the decoded JSON shape against
their own schema.
"""

from __future__ import annotations

import contextlib
import fcntl
import itertools
import json
import os
from pathlib import Path
from typing import Any, Iterator


#: Default permissions applied to the destination file (and its tmp counterpart).
DEFAULT_FILE_MODE = 0o600

#: Module-level counter so each call to :func:`_tmp_path` returns a unique
#: name within the current process. Combined with :data:`os.getpid` this
#: keeps tmp files unique across concurrent processes and concurrent calls
#: within the same process.
_TMP_COUNTER = itertools.count()


# --- Public API --------------------------------------------------------------


def read_json(
    path: str | os.PathLike[str],
    *,
    default: Any,
    lock_path: str | os.PathLike[str] | None = None,
) -> Any:
    """Return the JSON-decoded contents of ``path``; fall back to ``default``.

    ``OSError`` (missing file, permission denied, …) and
    :class:`json.JSONDecodeError` both map to ``default`` so callers can
    treat storage as best-effort and never crash on a transient read
    failure or a corrupted file.

    If ``lock_path`` is provided, the read is wrapped in an advisory
    :func:`fcntl.flock` against that file. Lock acquisition failures
    degrade silently to no-lock — storage failures must not crash the
    automation.
    """

    target = Path(path)
    with _maybe_lock(lock_path):
        return _read_json_unlocked(target, default)


def write_json_atomic(
    path: str | os.PathLike[str],
    payload: Any,
    *,
    lock_path: str | os.PathLike[str] | None = None,
    mode: int = DEFAULT_FILE_MODE,
) -> None:
    """Atomically write ``payload`` to ``path`` as JSON.

    The file is created with ``mode`` via :func:`os.open` so the perms are
    correct regardless of the process umask; the destination is then
    atomically swapped into place via :func:`os.replace`; and a final
    :func:`os.chmod` tightens the destination to ``mode`` as defense in
    depth against a pre-existing file that the umask widened.

    If ``lock_path`` is provided, the whole read-modify-write-equivalent
    block is wrapped in an advisory :func:`fcntl.flock`. Lock failures
    degrade to no-lock.
    """

    target = Path(path)
    with _maybe_lock(lock_path):
        _write_json_atomic_unlocked(target, payload, mode)


# --- Internal helpers --------------------------------------------------------


@contextlib.contextmanager
def _maybe_lock(
    lock_path: str | os.PathLike[str] | None,
) -> Iterator[None]:
    """Acquire an advisory ``fcntl.flock`` on ``lock_path`` if provided.

    The lock is best-effort: any :class:`OSError` during open/lock degrades
    to a no-op context so storage failures cannot crash the calling code.
    The matching ``flock(LOCK_UN)`` is also best-effort so a process that
    loses the lock (e.g., another holder died) still cleans up its file
    descriptor.
    """

    if lock_path is None:
        yield
        return

    lock_target = Path(lock_path)
    fd: int | None = None
    try:
        lock_target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_target), os.O_CREAT | os.O_RDWR, DEFAULT_FILE_MODE)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        yield
        return
    try:
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        with contextlib.suppress(OSError):
            os.close(fd)


def _read_json_unlocked(path: Path, default: Any) -> Any:
    """Read and decode ``path``; return ``default`` on any failure."""

    try:
        if not path.exists():
            return default
        # Tighten permissions on every read so a previously-loose file is
        # repaired even if the module that wrote it never re-loaded it.
        _enforce_mode(path)
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_atomic_unlocked(path: Path, payload: Any, mode: int) -> None:
    """Write ``payload`` to ``path`` via tmp + ``os.replace``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _tmp_path(path)
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except BaseException:
        # A partial tmp file must never be left behind.
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    # ``os.replace`` is atomic on POSIX and overwrites the destination.
    os.replace(tmp_path, path)
    _enforce_mode(path, mode)


def _tmp_path(path: Path) -> Path:
    """Return a unique sibling tmp path for ``path``.

    The tmp name embeds the current process id and a per-process counter
    so concurrent calls within the same process — and concurrent calls
    across processes — never collide on the same tmp file. The suffix
    falls back to ``.json`` when ``path`` has no extension so the helper
    works for callers that pass extension-less file names.
    """

    suffix = path.suffix or ".json"
    return path.with_name(f"{path.stem}.tmp.{os.getpid()}.{next(_TMP_COUNTER)}{suffix}")


def _enforce_mode(path: Path, mode: int = DEFAULT_FILE_MODE) -> None:
    """Tighten ``path``'s permissions to ``mode`` if they differ.

    Best-effort: any :class:`OSError` (file vanished between calls, EPERM
    on a foreign filesystem) is swallowed so a permission repair never
    crashes the calling automation.
    """

    try:
        current_mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if current_mode != mode:
        with contextlib.suppress(OSError):
            os.chmod(path, mode)


__all__ = [
    "DEFAULT_FILE_MODE",
    "read_json",
    "write_json_atomic",
]