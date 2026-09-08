"""Append-only JSONL journal of automation runs.

The journal is the on-disk source of truth for the funnel-metrics view (issue
#35, PR-E1). Every call to :meth:`jobtrail_ai_scorer.automation.JobTrailAutomation.run`
will (in PR-E3) write exactly one immutable JSON line to
``/DATA/AppData/jobtrail/logs/automated-job-search/runs.jsonl`` capturing the
counts the metric view needs:

    * ``searched`` / ``imported`` / ``scored`` straight from :class:`AutomationRun`
    * ``deduplicated`` — derived from per-profile ``duplicates`` when
      :attr:`AutomationRun.profile_counts` is populated, else ``searched - imported``
      (clamped to ``>= 0``)
    * ``scored_failed`` — count of failures whose stage label is ``score:``
    * ``notified`` — ``True`` iff there was a selected match or at least one
      failure (the same condition that triggers a notification today)
    * ``notification_kind`` — classification of the run outcome for the metric
      view's breakdown. The priority is
      ``match`` > ``breaker`` > ``preflight`` > ``failure`` > ``none`` so the
      metric view never mis-classifies a breaker-opened run as a plain failure.

Atomicity / permissions follow the contract established by
:mod:`jobtrail_ai_scorer._atomic_json`: each append reads the existing JSONL
content, appends a new line, and writes the whole file atomically via
``tmp + os.replace``; the on-disk mode is tightened to ``0600`` regardless of
umask; ``lock_path`` (when provided) wraps the read-modify-write block in an
advisory ``fcntl.flock`` so two overlapping runs cannot interleave.

The reader (:func:`iter_runs`) skips corrupted lines instead of raising so a
half-written entry never crashes the metric view, and applies a half-open
period filter ``[since, until)`` against ``started_at``.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from ._atomic_json import DEFAULT_FILE_MODE


# Default path lives outside the repository under the runtime logs directory.
DEFAULT_RUN_JOURNAL_PATH = (
    "/DATA/AppData/jobtrail/logs/automated-job-search/runs.jsonl"
)

# Bump when the on-disk schema changes in a non-backward-compatible way.
SCHEMA_VERSION = 1

# Stage prefix used by :func:`jobtrail_ai_scorer.automation._format_failure`
# to label score failures. ``scored_failed`` counts entries that begin with this
# prefix so the metric view's "scored vs scored-failed" breakdown is derived
# from the same failure labels the rest of the automation already produces.
_SCORE_FAILURE_PREFIX = "score:"

# Stage prefix used to label a breaker-opened short-circuit run.
_BREAKER_FAILURE_PREFIX = "breaker:"

# Stage prefix used to label a preflight-aborted short-circuit run.
_PREFLIGHT_FAILURE_PREFIX = "preflight:unavailable:"


def record_run(
    path: str | os.PathLike[str],
    run: Any,
    *,
    started_at: datetime | float,
    finished_at: datetime | float,
    base_url_source: str,
    clock: Callable[[], datetime] | None = None,
    lock_path: str | os.PathLike[str] | None = None,
) -> None:
    """Append one immutable JSONL line for ``run`` to the journal at ``path``.

    See the module docstring for the field contract. The call is atomic:
    a single ``tmp + os.replace`` swap either installs the new line alongside
    every previous line or leaves the journal untouched. The file (and its
    lock, when ``lock_path`` is provided) is created with ``0600`` permissions.
    """

    payload = _line_for_run(
        run,
        started_at=started_at,
        finished_at=finished_at,
        base_url_source=base_url_source,
        clock=clock,
    )
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with _maybe_lock(lock_path):
        _append_jsonl_line(path, line)


def iter_runs(
    path: str | os.PathLike[str],
    *,
    since: float | None = None,
    until: float | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield decoded journal lines; corrupted lines are skipped.

    ``since`` is inclusive; ``until`` is exclusive — i.e. the yielded lines
    satisfy ``since <= line.started_at < until``. Either bound can be ``None``
    (no lower / upper bound). A missing journal file yields nothing and does
    not raise so the metric view can render an "empty period" cleanly.
    """

    target = Path(path)
    if not target.exists():
        return
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if not _within_period(parsed, since=since, until=until):
            continue
        yield parsed


# --- line construction --------------------------------------------------------


def _line_for_run(
    run: Any,
    *,
    started_at: datetime | float,
    finished_at: datetime | float,
    base_url_source: str,
    clock: Callable[[], datetime] | None,
) -> dict[str, Any]:
    """Return the journal-line mapping for ``run``."""

    failures = _failure_list(run)
    selected = getattr(run, "selected", None)
    searched = int(getattr(run, "searched", 0) or 0)
    imported = int(getattr(run, "imported", 0) or 0)
    scored = int(getattr(run, "scored", 0) or 0)
    profile_counts = getattr(run, "profile_counts", None) or {}

    deduplicated = _deduplicated(
        searched=searched,
        imported=imported,
        profile_counts=profile_counts,
    )
    scored_failed = sum(1 for f in failures if f.startswith(_SCORE_FAILURE_PREFIX))
    notified = bool(selected) or bool(failures)
    notification_kind = _classify_notification_kind(
        selected=selected, failures=failures
    )

    run_id = _build_run_id(started_at=started_at, clock=clock)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": _to_timestamp(started_at),
        "finished_at": _to_timestamp(finished_at),
        "searched": searched,
        "imported": imported,
        "deduplicated": deduplicated,
        "scored": scored,
        "scored_failed": scored_failed,
        "notified": notified,
        "notification_kind": notification_kind,
        "failures": failures,
        "base_url_source": str(base_url_source),
    }


def _failure_list(run: Any) -> list[str]:
    """Return ``run.failures`` as a JSON-safe list of strings."""

    raw = getattr(run, "failures", ()) or ()
    return [str(label) for label in raw]


def _deduplicated(
    *,
    searched: int,
    imported: int,
    profile_counts: dict[str, dict[str, int]],
) -> int:
    """Return the deduplicated count for one run.

    When ``profile_counts`` is populated, the per-profile ``duplicates`` keys
    are summed — that's the most accurate source because the orchestrator only
    populates ``profile_counts`` when ``SearchProfile`` entries are configured.
    When no profiles are configured, ``searched - imported`` is the only signal
    the orchestrator provides, clamped to ``>= 0`` because a bookkeeping race
    (e.g., an upstream job spy returning a partial batch) should not produce a
    negative metric.
    """

    if profile_counts:
        total = 0
        for counts in profile_counts.values():
            if isinstance(counts, dict):
                total += int(counts.get("duplicates", 0) or 0)
        return total
    return max(0, searched - imported)


def _classify_notification_kind(
    *,
    selected: Any,
    failures: list[str],
) -> str:
    """Return the notification kind for one run.

    Priority is ``match`` > ``breaker`` > ``preflight`` > ``failure`` > ``none``
    so the metric view's breakdown never mis-classifies a short-circuit run as
    a plain failure. ``match`` wins regardless of accompanying failures so a
    successful match with a follow-up score error still reports as a match.
    """

    if selected is not None:
        return "match"
    if any(f.startswith(_BREAKER_FAILURE_PREFIX) for f in failures):
        return "breaker"
    if any(f.startswith(_PREFLIGHT_FAILURE_PREFIX) for f in failures):
        return "preflight"
    if failures:
        return "failure"
    return "none"


def _build_run_id(
    *,
    started_at: datetime | float,
    clock: Callable[[], datetime] | None,
) -> str:
    """Return a deterministic ``YYYY-MM-DD-HHMM-<short hex>`` run identifier.

    The seed is ``started_at``'s string form so the same starting instant
    always produces the same run id (callers correlate by it). When
    ``started_at`` is a :class:`datetime` we use it as the moment; when it is
    a Unix timestamp we fall back to ``clock()`` (or :func:`datetime.now` if
    ``clock`` is not provided) so the stamp portion is still wall-clock real.
    """

    # Local import keeps ``jobtrail_ai_scorer.notify`` from being required to
    # import this module for the metrics view to load its journal.
    from .notify import build_run_id

    if isinstance(started_at, datetime):
        return build_run_id(moment=started_at, seed=started_at.isoformat())
    return build_run_id(seed=str(started_at), clock=clock)


def _to_timestamp(value: datetime | float) -> float:
    """Coerce ``value`` to a Unix timestamp for storage / comparison."""

    if isinstance(value, datetime):
        return value.timestamp()
    return float(value)


def _within_period(
    parsed: dict[str, Any],
    *,
    since: float | None,
    until: float | None,
) -> bool:
    """Return True iff ``parsed['started_at']`` lies in ``[since, until)``."""

    started_at = parsed.get("started_at")
    if not isinstance(started_at, (int, float)) or isinstance(started_at, bool):
        return True  # not a number we can compare; let downstream decide
    if since is not None and started_at < since:
        return False
    if until is not None and started_at >= until:
        return False
    return True


# --- atomic JSONL writer -------------------------------------------------------


def _append_jsonl_line(path: str | os.PathLike[str], line: str) -> None:
    """Append ``line`` to the JSONL file at ``path`` atomically.

    The existing content is read, the new line is appended (with a single
    trailing newline if needed), and the whole file is rewritten via the same
    ``tmp + os.replace`` pattern that :mod:`_atomic_json` uses. The destination
    is tightened to ``0600`` regardless of umask so the journal inherits the
    SeenCache / CircuitBreaker permission contract.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    new_content = existing + ("" if existing.endswith("\n") or not existing else "\n") + line + "\n"
    tmp_path = _tmp_path(target)
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, DEFAULT_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_content)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    os.replace(tmp_path, target)
    _enforce_mode(target)


def _tmp_path(path: Path) -> Path:
    """Return a unique sibling tmp path for ``path``.

    Mirrors :func:`jobtrail_ai_scorer._atomic_json._tmp_path` (process id plus
    a per-process counter) so concurrent calls within the same process — and
    concurrent calls across processes — never collide on the same tmp file.
    The suffix falls back to ``.jsonl`` when ``path`` has no extension so the
    helper works for callers that pass extension-less file names.
    """

    import itertools

    counter = itertools.count()
    suffix = path.suffix or ".jsonl"
    return path.with_name(f"{path.stem}.tmp.{os.getpid()}.{next(counter)}{suffix}")


def _enforce_mode(path: Path, mode: int = DEFAULT_FILE_MODE) -> None:
    """Tighten ``path``'s permissions to ``mode`` if they differ.

    Best-effort: any :class:`OSError` is swallowed so a permission repair
    never crashes the calling automation.
    """

    try:
        current_mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if current_mode != mode:
        with contextlib.suppress(OSError):
            os.chmod(path, mode)


@contextlib.contextmanager
def _maybe_lock(
    lock_path: str | os.PathLike[str] | None,
) -> Iterator[None]:
    """Acquire an advisory ``fcntl.flock`` on ``lock_path`` if provided.

    Best-effort: lock acquisition failures degrade to a no-op so storage
    problems never crash the calling automation. Mirrors
    :func:`jobtrail_ai_scorer._atomic_json._maybe_lock` exactly so callers
    can mix-and-match the journal and SeenCache locks without re-deriving the
    contract.
    """

    if lock_path is None:
        yield
        return
    import fcntl

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


__all__ = [
    "DEFAULT_RUN_JOURNAL_PATH",
    "SCHEMA_VERSION",
    "iter_runs",
    "record_run",
]