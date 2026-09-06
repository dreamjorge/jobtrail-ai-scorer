"""TTL-based seen-cache that dedups offers before they reach ``/api/discover/import``.

The cache stores ``(source, sourceJobId)`` pairs in a JSON file with ``0600``
permissions. Each entry records the timestamp of its first sighting. A pair is
considered "fresh" (and therefore eligible to be skipped) while
``now - first_seen < hours_old * 2``. The full TTL formula mirrors the spec:

    TTL = max(now - first_seen, hours_old * 2)
    fresh  iff  now < first_seen + TTL

The cache lives outside the repository at the operator-controlled path
``/DATA/AppData/jobtrail/logs/automated-job-search/seen.json``. Writes are
atomic (``tmp + rename``) and the file is always tightened to ``0600``.

A cache that fails to read, contains invalid JSON, or is missing its parent
directory must never crash the calling automation. ``SeenCache`` treats every
failure as "start empty and try again next write".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Callable, Iterable, Mapping


# Default path lives outside the repository under the runtime logs directory.
DEFAULT_SEEN_CACHE_PATH = (
    "/DATA/AppData/jobtrail/logs/automated-job-search/seen.json"
)

# Separator used to build the (source, sourceJobId) cache key.
SEEN_CACHE_KEY_SEPARATOR = "\x1f"  # ASCII Unit Separator; never appears in job ids.

# Permissions applied to the cache file and its tmp counterpart.
SEEN_CACHE_FILE_MODE = 0o600

# Bump when the on-disk schema changes in a non-backward-compatible way.
SEEN_CACHE_SCHEMA_VERSION = 1


class SeenCache:
    """Persistent TTL set of ``(source, sourceJobId)`` pairs."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        clock: Callable[[], float] | None = None,
        file_mode: int = SEEN_CACHE_FILE_MODE,
    ) -> None:
        self._path = Path(path) if path is not None else Path(DEFAULT_SEEN_CACHE_PATH)
        self._clock: Callable[[], float] = clock if clock is not None else time.time
        self._file_mode = file_mode
        # ``_entries`` maps the joined cache key -> {"first_seen": float}.
        self._entries: dict[str, dict[str, float]] = {}
        self._load()

    # --- public API ---------------------------------------------------------

    def should_skip(
        self,
        source: str,
        source_job_id: str,
        *,
        hours_old: int,
        now: float | None = None,
    ) -> bool:
        """Return True if the pair should be skipped (still in its TTL window)."""

        if hours_old < 0:
            raise ValueError("hours_old must be non-negative")

        current = self._current_time(now)
        entry = self._entries.get(self._key(source, source_job_id))
        if entry is None:
            return False
        first_seen = entry["first_seen"]
        age = current - first_seen
        # Spec: TTL = max(now - first_seen, hours_old * 2). Entry is fresh iff
        # ``now < first_seen + TTL``. For entries older than hours_old*2, TTL
        # equals their age and ``first_seen + TTL == now``, so they fall out of
        # the fresh window on the very next check.
        ttl = max(age, hours_old * 2 * 3600)
        return current < first_seen + ttl

    def mark_seen(
        self,
        source: str,
        source_job_id: str,
        *,
        now: float | None = None,
    ) -> None:
        """Record ``(source, sourceJobId)`` as seen and persist the cache."""

        key = self._key(source, source_job_id)
        current = self._current_time(now)
        # ``mark_seen`` is only called after ``should_skip`` returned False,
        # i.e. the pair is either new or its TTL already expired. Refresh
        # ``first_seen`` unconditionally so an expired entry starts a new TTL
        # window instead of staying permanently expired (which would make the
        # offer get reimported/rescored on every subsequent run forever).
        self._entries[key] = {"first_seen": current}
        self.save()

    def reset(self) -> None:
        """Clear every entry from the cache and persist the empty state."""

        self._entries = {}
        self.save()

    def save(self) -> None:
        """Atomically write the cache to disk (tmp + rename)."""

        payload = {
            "version": SEEN_CACHE_SCHEMA_VERSION,
            "entries": self._entries,
        }
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._tmp_path(path)
        # Open with O_CREAT|O_TRUNC|O_WRONLY and the desired mode so the tmp
        # file is also 0600, regardless of the process umask.
        fd = os.open(
            str(tmp_path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            self._file_mode,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
        except BaseException:
            # If writing the tmp file failed, remove it so we never leak partial bytes.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        # ``os.replace`` is atomic on POSIX and overwrites the destination.
        os.replace(tmp_path, path)
        # Defense in depth: the umask can still widen the perms if the file
        # already existed before we wrote it.
        self._enforce_mode(path)

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def path(self) -> Path:
        return self._path

    # --- internal helpers ---------------------------------------------------

    def _key(self, source: str, source_job_id: str) -> str:
        return f"{source}{SEEN_CACHE_KEY_SEPARATOR}{source_job_id}"

    def _current_time(self, now: float | None) -> float:
        if now is None:
            return float(self._clock())
        return float(now)

    def _tmp_path(self, path: Path) -> Path:
        suffix = path.suffix or ".json"
        # Unique per writer (pid + object id) so concurrent/overlapping runs
        # never share the same tmp file and interleave writes.
        return path.with_name(f"{path.stem}.tmp.{os.getpid()}.{id(self)}{suffix}")

    def _enforce_mode(self, path: Path) -> None:
        try:
            current_mode = path.stat().st_mode & 0o777
        except OSError:
            return
        if current_mode != self._file_mode:
            try:
                os.chmod(path, self._file_mode)
            except OSError:
                # Best-effort: a failed chmod must not crash the run.
                pass

    def _load(self) -> None:
        path = self._path
        try:
            if not path.exists():
                self._entries = {}
                return
            # Tighten permissions on every load so we never serve a world-readable
            # cache even if something previously left it loose.
            self._enforce_mode(path)
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            # Corruption: degrade to empty cache, never raise.
            self._entries = {}
            return

        self._entries = _validate_entries(data)


def _validate_entries(raw: object) -> dict[str, dict[str, float]]:
    """Return only the well-formed entries from a decoded JSON payload."""

    if not isinstance(raw, Mapping):
        return {}
    entries_obj = raw.get("entries") if hasattr(raw, "get") else None
    if not isinstance(entries_obj, Mapping):
        return {}

    validated: dict[str, dict[str, float]] = {}
    for key, value in entries_obj.items():
        if not isinstance(key, str) or not isinstance(value, Mapping):
            continue
        first_seen = value.get("first_seen")
        if isinstance(first_seen, bool):
            # bools are ints in Python; exclude them explicitly so True/False
            # cannot masquerade as a timestamp.
            continue
        if isinstance(first_seen, (int, float)):
            validated[key] = {"first_seen": float(first_seen)}
    return validated


def keys_for_inspection(
    entries: Iterable[tuple[str, dict[str, float]]],
) -> list[tuple[str, str, float]]:
    """Split stored keys back into ``(source, source_job_id, first_seen)`` triples."""

    result: list[tuple[str, str, float]] = []
    for key, value in entries:
        if SEEN_CACHE_KEY_SEPARATOR in key:
            source, source_job_id = key.split(SEEN_CACHE_KEY_SEPARATOR, 1)
            first_seen = value.get("first_seen", 0.0) if isinstance(value, Mapping) else 0.0
            result.append((source, source_job_id, float(first_seen)))
    return result