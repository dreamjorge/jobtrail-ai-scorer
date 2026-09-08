"""Read-only preflight checks for the JobTrail automation pipeline.

The preflight module performs a small set of bounded, side-effect-free probes
against the dependencies the daily automation relies on before it invests in a
full search/import/score/notify run:

* ``jobtrail_api`` — ``GET /api/health`` with a 2-second timeout, falling back
  to ``HEAD`` when the GET returns 404 so older backends (no health endpoint)
  still report ``healthy`` instead of ``degraded``.
* ``jobspy_search`` — a minimal ``JobSpySourceAdapter.search`` invocation with
  ``RetryPolicy(max_attempts=1)`` and a 5-second timeout. Surfaces search
  gateway connectivity errors as ``unavailable``.
* ``hermes_provider`` — ``hermes --profile <profile> --help`` with a 2-second
  timeout; healthy iff ``returncode == 0``.
* ``whatsapp`` — only added when ``notify_enabled=True``; ``os.access(...,
  os.X_OK)`` keeps the probe dependency-free.

Each probe runs through :class:`concurrent.futures.Future` with an explicit
per-check timeout so a wedged upstream can never block the orchestrator. The
runner maps probe exceptions to one of three terminal statuses:

* ``healthy`` — the probe returned successfully.
* ``degraded`` — the probe raised ``httpx.HTTPStatusError`` with a 4xx code
  (the upstream is misconfigured but reachable).
* ``unavailable`` — the probe raised a 5xx code, transport error
  (``TimeoutException``/``ConnectError``/``OSError``),
  ``subprocess.TimeoutExpired``/``FileNotFoundError``, or exceeded the
  per-check timeout enforced via :class:`concurrent.futures`.

The aggregator (:class:`PreflightReport`) reduces the per-check results into
``overall_status`` (worst wins: ``unavailable`` > ``degraded`` > ``healthy``)
and ``should_abort`` (True iff any *required* check is ``unavailable`` so a
hard outage on a critical dependency short-circuits the rest of the run).
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import os
import subprocess
from typing import Any, Callable, Iterable, Mapping

import httpx

from .automation import AutomationConfig
from .retry import RetryPolicy
from .sources import JobSpySourceAdapter, SourceSearchRequest


# --- Duck-typed config access -----------------------------------------------


def _config_attr(config: Any, name: str, default: Any) -> Any:
    """Return ``config.<name>`` if present, otherwise ``default``.

    Preflight must work for both :class:`jobtrail_ai_scorer.config.AppConfig`
    (which carries ``hermes_executable``/``hermes_profile``) and
    :class:`jobtrail_ai_scorer.automation.AutomationConfig` (which does not
    in PR-D1). Duck-typing with explicit defaults lets the same factory build
    the probes in both contexts without a hard import on either config
    subclass.
    """

    return getattr(config, name, default)


#: Status returned when the probe completes without raising.
STATUS_HEALTHY = "healthy"
#: Status returned when the probe raises an HTTP 4xx response.
STATUS_DEGRADED = "degraded"
#: Status returned on transport errors, 5xx, subprocess failures, or timeouts.
STATUS_UNAVAILABLE = "unavailable"

#: Ranked status severity used to compute ``overall_status``.
_STATUS_SEVERITY: Mapping[str, int] = {
    STATUS_HEALTHY: 0,
    STATUS_DEGRADED: 1,
    STATUS_UNAVAILABLE: 2,
}


@dataclasses.dataclass(frozen=True)
class PreflightCheck:
    """One probe definition.

    ``probe`` is a zero-argument callable. Returning normally signals
    ``healthy``; raising an exception is mapped to a status by
    :func:`_classify_exception`. ``timeout_seconds`` is enforced by
    :func:`run_preflight` via :class:`concurrent.futures`.
    """

    name: str
    probe: Callable[[], None]
    timeout_seconds: float
    required: bool = True


@dataclasses.dataclass(frozen=True)
class PreflightResult:
    """The outcome of one preflight probe."""

    name: str
    status: str
    detail: str = ""


class PreflightReport(tuple):
    """Ordered collection of :class:`PreflightResult` values.

    The report subclasses ``tuple`` so existing tuple-based access patterns
    (``len(report)``, iteration, indexing) keep working. ``required`` carries
    the per-name ``required`` flags from the originating checks; missing
    entries default to ``True`` so the ``required=False`` opt-out must be
    explicit.
    """

    def __new__(
        cls,
        results: Iterable[PreflightResult],
        *,
        required: Mapping[str, bool] | None = None,
    ) -> "PreflightReport":
        materialized = tuple(results)
        instance = super().__new__(cls, materialized)
        instance._required: dict[str, bool] = dict(required or {})
        return instance

    @property
    def overall_status(self) -> str:
        """Return the worst status seen across all results."""

        if not self:
            return STATUS_HEALTHY
        worst_rank = max(
            _STATUS_SEVERITY.get(r.status, _STATUS_SEVERITY[STATUS_UNAVAILABLE])
            for r in self
        )
        for status, rank in _STATUS_SEVERITY.items():
            if rank == worst_rank:
                return status
        return STATUS_UNAVAILABLE  # pragma: no cover - unreachable

    @property
    def should_abort(self) -> bool:
        """Return True iff any required check is unavailable."""

        return any(
            r.status == STATUS_UNAVAILABLE and self._required.get(r.name, True)
            for r in self
        )


# --- Exception → status classifier -----------------------------------------


def _classify_exception(exc: BaseException) -> tuple[str, str]:
    """Map a probe exception to ``(status, detail)``.

    HTTP 4xx is ``degraded`` (the upstream is reachable but unhappy); HTTP 5xx,
    transport errors, and subprocess failures are ``unavailable``. Any other
    exception defaults to ``unavailable`` so an unexpected failure cannot be
    silently treated as healthy.
    """

    if isinstance(exc, httpx.HTTPStatusError):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            if 400 <= status_code < 500:
                return STATUS_DEGRADED, f"http_{status_code}"
            if 500 <= status_code < 600:
                return STATUS_UNAVAILABLE, f"http_{status_code}"
            return STATUS_UNAVAILABLE, f"http_{status_code}"
        return STATUS_UNAVAILABLE, "http_status_unknown"

    if isinstance(exc, httpx.TimeoutException):
        return STATUS_UNAVAILABLE, "timeout"
    if isinstance(exc, httpx.ConnectError):
        return STATUS_UNAVAILABLE, "connect_error"
    if isinstance(exc, httpx.RequestError):
        return STATUS_UNAVAILABLE, "request_error"
    if isinstance(exc, (subprocess.TimeoutExpired, FileNotFoundError, OSError)):
        return STATUS_UNAVAILABLE, type(exc).__name__.lower()

    return STATUS_UNAVAILABLE, type(exc).__name__.lower()


# --- Default probes --------------------------------------------------------


def _jobtrail_api_probe(base_url: str, *, timeout_seconds: float) -> Callable[[], None]:
    """Build the ``jobtrail_api`` probe.

    Issues ``GET /api/health``; on 404, retries with ``HEAD`` so a backend
    without the health endpoint still yields ``healthy``. Any non-2xx response
    is surfaced via :class:`httpx.HTTPStatusError`.
    """

    url = f"{base_url.rstrip('/')}/api/health"

    def probe() -> None:
        try:
            response = httpx.get(url, timeout=timeout_seconds)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError, OSError):
            raise
        if response.status_code == 404:
            try:
                response = httpx.head(url, timeout=timeout_seconds)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError, OSError):
                raise
        response.raise_for_status()

    return probe


def _jobspy_search_probe(
    base_url: str, *, timeout_seconds: float
) -> Callable[[], None]:
    """Build the ``jobspy_search`` probe.

    Uses :class:`JobSpySourceAdapter` over a small
    :class:`SearchGateway` that calls ``POST /api/discover/search`` via the
    module-level :func:`httpx.post` so tests can monkey-patch the transport.
    ``RetryPolicy(max_attempts=1)`` keeps the probe single-shot; the
    5-second HTTP timeout bounds the call.
    """

    policy = RetryPolicy(max_attempts=1)
    url = f"{base_url.rstrip('/')}/api/discover/search"

    def _search(payload: dict[str, Any]) -> list[Any]:
        response = httpx.post(url, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("results", data.get("jobs", []))
        return []

    gateway = _DirectSearchGateway(_search)
    adapter = JobSpySourceAdapter(gateway)

    request = SourceSearchRequest(
        sites=("linkedin",),
        search_term="preflight",
        location="remote",
        results_wanted=1,
        hours_old=72,
        is_remote=True,
        profile_name="preflight",
    )

    def probe() -> None:
        adapter.search(request)

    # ``policy`` is referenced through the closure to keep its identity stable
    # for callers that want to introspect it via tests.
    probe._retry_policy = policy  # type: ignore[attr-defined]
    return probe


class _DirectSearchGateway:
    """Minimal :class:`SearchGateway` that delegates to a search callable.

    The preflight jobspy probe does not need the full automation gateway
    (search, import, get_job) — only ``search``. This adapter keeps the
    dependency surface small and lets tests substitute the search function
    trivially.
    """

    def __init__(self, search: Callable[[dict[str, Any]], list[Any]]) -> None:
        self._search = search

    def search(self, payload: dict[str, Any]) -> list[Any]:
        return self._search(payload)


def _hermes_provider_probe(
    executable: str, profile: str, *, timeout_seconds: float
) -> Callable[[], None]:
    """Build the ``hermes_provider`` probe.

    Runs ``<executable> --profile <profile> --help`` with the given timeout;
    raises :class:`RuntimeError` when the executable is missing or returns a
    non-zero exit code so the runner can mark the check ``unavailable``.
    """

    command = [executable, "--profile", profile, "--help"]

    def probe() -> None:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_seconds,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            raise
        if result.returncode != 0:
            raise RuntimeError(
                f"hermes probe failed with returncode={result.returncode}"
            )

    return probe


def _whatsapp_probe(whatsapp_command: str) -> Callable[[], None]:
    """Build the optional ``whatsapp`` probe via ``os.access(..., os.X_OK)``."""

    path = whatsapp_command

    def probe() -> None:
        if not os.access(path, os.X_OK):
            raise RuntimeError(f"whatsapp command not executable: {path!r}")

    return probe


def default_preflight_checks(
    config: AutomationConfig,
) -> tuple[PreflightCheck, ...]:
    """Return the read-only probe set for ``config``.

    ``whatsapp`` is added only when ``config.notify_enabled`` is True. The
    per-check timeouts are tuned to match the spec: 2s for ``jobtrail_api``
    and ``hermes_provider``, 5s for ``jobspy_search``, and 1s for
    ``whatsapp`` (a synchronous ``os.access`` call is effectively instant, but
    the timeout is set explicitly to keep the executor API uniform).
    """

    checks: list[PreflightCheck] = [
        PreflightCheck(
            name="jobtrail_api",
            probe=_jobtrail_api_probe(
                config.base_url, timeout_seconds=2.0
            ),
            timeout_seconds=2.0,
            required=True,
        ),
        PreflightCheck(
            name="jobspy_search",
            probe=_jobspy_search_probe(
                config.base_url, timeout_seconds=5.0
            ),
            timeout_seconds=5.0,
            required=True,
        ),
        PreflightCheck(
            name="hermes_provider",
            probe=_hermes_provider_probe(
                _config_attr(config, "hermes_executable", "hermes"),
                _config_attr(config, "hermes_profile", "default"),
                timeout_seconds=2.0,
            ),
            timeout_seconds=2.0,
            required=True,
        ),
    ]
    if config.notify_enabled:
        checks.append(
            PreflightCheck(
                name="whatsapp",
                probe=_whatsapp_probe(config.whatsapp_command),
                timeout_seconds=1.0,
                required=False,
            )
        )
    return tuple(checks)


# --- Runner -----------------------------------------------------------------


def _create_default_executor(max_workers: int) -> concurrent.futures.Executor:
    """Build the executor used when ``run_preflight`` is called without one.

    Factored out as a module-level function so tests can monkeypatch the
    default-executor construction without threading through private helpers.
    """

    return concurrent.futures.ThreadPoolExecutor(max_workers=max_workers or 1)


def run_preflight(
    config: AutomationConfig,
    *,
    checks: tuple[PreflightCheck, ...] | None = None,
    executor: concurrent.futures.Executor | None = None,
) -> PreflightReport:
    """Execute ``checks`` and return a :class:`PreflightReport`.

    Each check is submitted to ``executor`` (or a freshly created
    :class:`concurrent.futures.ThreadPoolExecutor` when ``executor`` is None)
    and awaited with ``future.result(timeout=check.timeout_seconds)``. A
    timeout is mapped to :data:`STATUS_UNAVAILABLE` with ``detail="timeout"``;
    any other exception is mapped by :func:`_classify_exception`.

    The provided ``executor`` is *not* shut down — it is owned by the caller.
    An internally created executor is shut down with ``wait=False`` so the
    runner never blocks on a wedged probe.
    """

    if checks is None:
        checks = default_preflight_checks(config)

    owns_executor = executor is None
    if owns_executor:
        executor = _create_default_executor(len(checks))

    futures: list[tuple[Any, PreflightCheck]] = []
    try:
        for check in checks:
            future = executor.submit(check.probe)
            futures.append((future, check))

        results: list[PreflightResult] = []
        for future, check in futures:
            try:
                future.result(timeout=check.timeout_seconds)
                results.append(PreflightResult(name=check.name, status=STATUS_HEALTHY))
            except concurrent.futures.TimeoutError:
                results.append(
                    PreflightResult(
                        name=check.name,
                        status=STATUS_UNAVAILABLE,
                        detail="timeout",
                    )
                )
            except Exception as exc:  # noqa: BLE001 — probe boundary
                status, detail = _classify_exception(exc)
                results.append(
                    PreflightResult(
                        name=check.name, status=status, detail=detail
                    )
                )

        required = {check.name: check.required for check in checks}
        return PreflightReport(results, required=required)
    finally:
        if owns_executor:
            executor.shutdown(wait=False)


__all__ = [
    "PreflightCheck",
    "PreflightReport",
    "PreflightResult",
    "STATUS_DEGRADED",
    "STATUS_HEALTHY",
    "STATUS_UNAVAILABLE",
    "default_preflight_checks",
    "run_preflight",
]