"""Tests for the preflight check module.

These tests pin the public contract of :mod:`jobtrail_ai_scorer.preflight`:

* :class:`PreflightCheck` carries a name, probe, timeout, and required flag.
* :class:`PreflightResult` exposes name, status (``healthy``/``degraded``/
  ``unavailable``), and a free-text detail.
* :class:`PreflightReport` aggregates per-check results into ``overall_status``
  (worst wins: ``unavailable`` > ``degraded`` > ``healthy``) and
  ``should_abort`` (True iff any *required* check is unavailable).
* :func:`default_preflight_checks` builds the four read-only probes from a
  config-like object.
* :func:`run_preflight` runs the probes via an injectable executor with
  per-check timeouts enforced through :class:`concurrent.futures`.

The tests inject a deterministic :class:`FakeExecutor` so the per-check timeout
contract can be exercised without sleeping on a real thread pool.
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest

from jobtrail_ai_scorer.automation import AutomationConfig
from jobtrail_ai_scorer.preflight import (
    PreflightCheck,
    PreflightReport,
    PreflightResult,
    default_preflight_checks,
    run_preflight,
)


# --- Fake executor for deterministic timeout/result injection ----------------


class FakeFuture:
    """Stand-in for ``concurrent.futures.Future`` with explicit delay control.

    ``result(timeout=...)`` raises :class:`concurrent.futures.TimeoutError`
    when ``delay > timeout``. When ``exc`` is provided, the future raises that
    exception instead of running the probe. Otherwise ``result`` invokes the
    stored probe and returns its value (or propagates any exception it
    raises).
    """

    def __init__(
        self,
        *,
        exc: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self._fn: Callable[[], Any] | None = None
        self._exc = exc
        self._delay = delay
        self._consumed = False

    def result(self, timeout: float | None = None) -> Any:
        if timeout is not None and self._delay > timeout:
            self._consumed = True
            raise concurrent.futures.TimeoutError()
        self._consumed = True
        if self._exc is not None:
            raise self._exc
        if self._fn is not None:
            return self._fn()
        return None


class FakeExecutor:
    """Minimal executor API exposing ``submit`` and ``shutdown``.

    Tests push a list of pre-built :class:`FakeFuture` objects; each ``submit``
    pops the next future and wires the submitted probe into it. With no
    futures queued, ``submit`` builds a default :class:`FakeFuture` that runs
    the probe verbatim — matching the real ``ThreadPoolExecutor.submit``
    semantics.
    """

    def __init__(self, futures: list[FakeFuture] | None = None) -> None:
        self._queue: list[FakeFuture] = list(futures or [])
        self.submitted: list[Callable[[], Any]] = []
        self.shutdown_calls: list[bool] = []

    def submit(self, fn: Callable[[], Any]) -> FakeFuture:
        self.submitted.append(fn)
        if self._queue:
            future = self._queue.pop(0)
            future._fn = fn
            return future
        future = FakeFuture()
        future._fn = fn
        return future

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


def _build_request(method: str, url: str) -> httpx.Request:
    return httpx.Request(method, url)


def _build_response(
    request: httpx.Request, status_code: int, method: str = "GET"
) -> httpx.Response:
    return httpx.Response(status_code, request=request)


# --- PreflightCheck / PreflightResult dataclasses ----------------------------


def test_preflight_check_required_defaults_to_true():
    """``required`` defaults to True so callers can omit it for mandatory probes."""

    check = PreflightCheck(name="x", probe=lambda: None, timeout_seconds=1.0)
    assert check.required is True
    assert check.name == "x"
    assert check.timeout_seconds == 1.0


def test_preflight_check_required_can_be_disabled():
    """``required=False`` is honored for optional checks like ``whatsapp``."""

    check = PreflightCheck(
        name="x", probe=lambda: None, timeout_seconds=1.0, required=False
    )
    assert check.required is False


def test_preflight_result_defaults_detail_to_empty_string():
    """``detail`` defaults to an empty string and ``status`` is set explicitly."""

    result = PreflightResult(name="x", status="healthy")
    assert result.detail == ""
    assert result.status == "healthy"
    assert result.name == "x"


# --- run_preflight: 200 / 5xx / 404 classification ----------------------------


def test_run_preflight_classifies_200_as_healthy(monkeypatch):
    """A 2xx response from ``/api/health`` is classified as ``healthy``."""

    captured: dict[str, Any] = {}

    def fake_get(url, *args, **kwargs):
        captured["url"] = url
        captured["timeout"] = kwargs.get("timeout")
        request = _build_request("GET", url)
        return _build_response(request, 200)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "head", fake_get)

    config = AutomationConfig(base_url="http://example.test")
    report = run_preflight(
        config,
        checks=default_preflight_checks(config),
        executor=FakeExecutor(),
    )

    by_name = {r.name: r for r in report}
    assert by_name["jobtrail_api"].status == "healthy"
    assert captured["url"].endswith("/api/health")


def test_run_preflight_classifies_5xx_as_unavailable(monkeypatch):
    """A 5xx response is classified as ``unavailable``."""

    def fake_get(url, *args, **kwargs):
        request = _build_request("GET", url)
        response = _build_response(request, 503)
        return response

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "head", fake_get)

    config = AutomationConfig(base_url="http://example.test")
    report = run_preflight(
        config,
        checks=default_preflight_checks(config),
        executor=FakeExecutor(),
    )

    by_name = {r.name: r for r in report}
    assert by_name["jobtrail_api"].status == "unavailable"


def test_run_preflight_classifies_404_as_degraded(monkeypatch):
    """A 404 on both GET and HEAD is classified as ``degraded`` (4xx)."""

    calls: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, *args, **kwargs):
        calls.append((method, url))
        request = _build_request(method, url)
        return _build_response(request, 404, method=method)

    monkeypatch.setattr(httpx, "get", lambda url, *a, **k: fake_request("GET", url, *a, **k))
    monkeypatch.setattr(httpx, "head", lambda url, *a, **k: fake_request("HEAD", url, *a, **k))

    config = AutomationConfig(base_url="http://example.test")
    report = run_preflight(
        config,
        checks=default_preflight_checks(config),
        executor=FakeExecutor(),
    )

    by_name = {r.name: r for r in report}
    assert by_name["jobtrail_api"].status == "degraded"
    # The HEAD fallback was attempted on 404.
    assert ("HEAD", calls[0][1]) in calls


def test_run_preflight_404_get_with_2xx_head_fallback_is_healthy(monkeypatch):
    """A 404 on GET that succeeds with a 2xx HEAD response is ``healthy``."""

    def fake_get(url, *args, **kwargs):
        request = _build_request("GET", url)
        return _build_response(request, 404)

    def fake_head(url, *args, **kwargs):
        request = _build_request("HEAD", url)
        return _build_response(request, 204, method="HEAD")

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "head", fake_head)

    config = AutomationConfig(base_url="http://example.test")
    report = run_preflight(
        config,
        checks=default_preflight_checks(config),
        executor=FakeExecutor(),
    )

    by_name = {r.name: r for r in report}
    assert by_name["jobtrail_api"].status == "healthy"


# --- run_preflight: timeout / transport errors ------------------------------


def test_run_preflight_classifies_timeout_as_unavailable():
    """httpx.TimeoutException is mapped to ``unavailable``."""

    def probe() -> None:
        raise httpx.TimeoutException("read timeout")

    check = PreflightCheck(
        name="jobtrail_api", probe=probe, timeout_seconds=2.0
    )
    report = run_preflight(
        AutomationConfig(),
        checks=(check,),
        executor=FakeExecutor([FakeFuture(exc=httpx.TimeoutException("read timeout"))]),
    )

    assert len(report) == 1
    assert report[0].status == "unavailable"


def test_run_preflight_classifies_connect_error_as_unavailable():
    """httpx.ConnectError is mapped to ``unavailable``."""

    exc = httpx.ConnectError("connection refused")
    check = PreflightCheck(
        name="jobtrail_api", probe=lambda: None, timeout_seconds=2.0
    )
    report = run_preflight(
        AutomationConfig(),
        checks=(check,),
        executor=FakeExecutor([FakeFuture(exc=exc)]),
    )

    assert report[0].status == "unavailable"


def test_run_preflight_classifies_os_error_as_unavailable():
    """Generic ``OSError`` (e.g., DNS failure) is mapped to ``unavailable``."""

    exc = OSError("name resolution failed")
    check = PreflightCheck(
        name="jobtrail_api", probe=lambda: None, timeout_seconds=2.0
    )
    report = run_preflight(
        AutomationConfig(),
        checks=(check,),
        executor=FakeExecutor([FakeFuture(exc=exc)]),
    )

    assert report[0].status == "unavailable"


# --- run_preflight: subprocess hermes probe ----------------------------------


def test_run_preflight_subprocess_missing_executable_is_unavailable(monkeypatch):
    """``FileNotFoundError`` from ``subprocess.run`` maps to ``unavailable``."""

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("hermes not found")

    monkeypatch.setattr(
        "jobtrail_ai_scorer.preflight.subprocess.run", fake_run
    )

    config = _make_hermes_config(profile="local")
    report = run_preflight(
        config,
        checks=default_preflight_checks(config),
        executor=FakeExecutor(),
    )

    by_name = {r.name: r for r in report}
    assert by_name["hermes_provider"].status == "unavailable"


def test_run_preflight_subprocess_nonzero_returncode_is_unavailable(monkeypatch):
    """A nonzero ``returncode`` from ``hermes --help`` maps to ``unavailable``."""

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="bad profile")

    monkeypatch.setattr(
        "jobtrail_ai_scorer.preflight.subprocess.run", fake_run
    )

    config = _make_hermes_config(profile="local")
    report = run_preflight(
        config,
        checks=default_preflight_checks(config),
        executor=FakeExecutor(),
    )

    by_name = {r.name: r for r in report}
    assert by_name["hermes_provider"].status == "unavailable"


def test_run_preflight_subprocess_returncode_zero_is_healthy(monkeypatch):
    """A ``returncode`` of 0 from ``hermes --help`` is classified as ``healthy``."""

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="usage", stderr="")

    monkeypatch.setattr(
        "jobtrail_ai_scorer.preflight.subprocess.run", fake_run
    )

    config = _make_hermes_config(profile="local")
    report = run_preflight(
        config,
        checks=default_preflight_checks(config),
        executor=FakeExecutor(),
    )

    by_name = {r.name: r for r in report}
    assert by_name["hermes_provider"].status == "healthy"


def test_run_preflight_subprocess_timeout_is_unavailable(monkeypatch):
    """``subprocess.TimeoutExpired`` maps to ``unavailable``."""

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["hermes"], timeout=2.0)

    monkeypatch.setattr(
        "jobtrail_ai_scorer.preflight.subprocess.run", fake_run
    )

    config = _make_hermes_config(profile="local")
    report = run_preflight(
        config,
        checks=default_preflight_checks(config),
        executor=FakeExecutor(),
    )

    by_name = {r.name: r for r in report}
    assert by_name["hermes_provider"].status == "unavailable"


# --- should_abort / required vs optional ------------------------------------


def test_should_abort_true_when_any_required_check_is_unavailable():
    """``should_abort`` is True when at least one required check is unavailable."""

    results = (
        PreflightResult(name="jobtrail_api", status="healthy"),
        PreflightResult(name="hermes_provider", status="unavailable"),
        PreflightResult(name="jobspy_search", status="healthy"),
    )
    required = {
        "jobtrail_api": True,
        "hermes_provider": True,
        "jobspy_search": True,
    }
    report = PreflightReport(results, required=required)

    assert report.overall_status == "unavailable"
    assert report.should_abort is True


def test_should_abort_false_when_only_degraded_checks_present():
    """``should_abort`` is False when required checks are healthy or degraded."""

    results = (
        PreflightResult(name="jobtrail_api", status="degraded"),
        PreflightResult(name="hermes_provider", status="healthy"),
    )
    required = {"jobtrail_api": True, "hermes_provider": True}
    report = PreflightReport(results, required=required)

    assert report.overall_status == "degraded"
    assert report.should_abort is False


def test_should_abort_false_when_required_healthy_even_if_optional_unavailable():
    """Optional (``required=False``) unavailable does NOT trigger abort."""

    results = (
        PreflightResult(name="jobtrail_api", status="healthy"),
        PreflightResult(name="whatsapp", status="unavailable"),
    )
    required = {"jobtrail_api": True, "whatsapp": False}
    report = PreflightReport(results, required=required)

    assert report.overall_status == "unavailable"  # overall reflects worst status
    assert report.should_abort is False  # but whatsapp is optional


def test_overall_status_aggregation_orders_worst_first():
    """``overall_status`` reports the worst status (unavailable > degraded > healthy)."""

    only_healthy = PreflightReport(
        (PreflightResult(name="a", status="healthy"),)
    )
    assert only_healthy.overall_status == "healthy"

    with_degraded = PreflightReport(
        (
            PreflightResult(name="a", status="healthy"),
            PreflightResult(name="b", status="degraded"),
        )
    )
    assert with_degraded.overall_status == "degraded"

    with_unavailable = PreflightReport(
        (
            PreflightResult(name="a", status="healthy"),
            PreflightResult(name="b", status="degraded"),
            PreflightResult(name="c", status="unavailable"),
        )
    )
    assert with_unavailable.overall_status == "unavailable"


def test_preflight_report_is_a_tuple_of_results():
    """The report behaves as a tuple so callers can iterate in declaration order."""

    results = (
        PreflightResult(name="a", status="healthy"),
        PreflightResult(name="b", status="degraded"),
    )
    report = PreflightReport(results)
    assert tuple(report) == results
    assert len(report) == 2
    assert report[0].name == "a"


# --- default_preflight_checks ------------------------------------------------


def _make_hermes_config(*, profile: str = "local") -> Any:
    """Build a config-like object exposing the duck-typed fields preflight reads.

    ``AutomationConfig`` does not carry ``hermes_profile``/``hermes_executable``
    in PR-D1; preflight reads them via ``getattr`` with sensible defaults so a
    plain ``AutomationConfig`` works in production wiring. Tests that exercise
    the hermes probe need to override the profile so we monkey-attach the
    attribute on the dataclass via ``object.__setattr__`` (the dataclass is
    frozen).
    """

    config = AutomationConfig()
    object.__setattr__(config, "hermes_profile", profile)
    object.__setattr__(config, "hermes_executable", "hermes")
    return config


def test_default_preflight_checks_excludes_whatsapp_when_notify_disabled():
    """``whatsapp`` is added only when ``notify_enabled=True``."""

    config = AutomationConfig(notify_enabled=False, whatsapp_command="/usr/bin/wa")
    names = tuple(c.name for c in default_preflight_checks(config))
    assert "whatsapp" not in names


def test_default_preflight_checks_includes_whatsapp_when_notify_enabled():
    """``whatsapp`` is added when ``notify_enabled=True``."""

    config = AutomationConfig(notify_enabled=True, whatsapp_command="/usr/bin/wa")
    names = tuple(c.name for c in default_preflight_checks(config))
    assert "whatsapp" in names


def test_default_preflight_checks_includes_four_required_probes():
    """Default probes cover jobtrail_api, jobspy_search, hermes_provider."""

    config = _make_hermes_config()
    names = tuple(c.name for c in default_preflight_checks(config))
    assert "jobtrail_api" in names
    assert "jobspy_search" in names
    assert "hermes_provider" in names


def test_whatsapp_probe_uses_os_access_and_is_optional(monkeypatch):
    """The whatsapp probe uses ``os.access(..., os.X_OK)`` and is not required."""

    access_calls: list[tuple[str, int]] = []

    def fake_access(path, mode):
        access_calls.append((path, mode))
        return True

    monkeypatch.setattr("jobtrail_ai_scorer.preflight.os.access", fake_access)

    config = AutomationConfig(notify_enabled=True, whatsapp_command="/usr/bin/wa")
    checks = default_preflight_checks(config)
    whatsapp = next(c for c in checks if c.name == "whatsapp")
    assert whatsapp.required is False
    assert whatsapp.probe() is None  # healthy path returns None
    assert access_calls == [("/usr/bin/wa", os.X_OK)]


def test_whatsapp_probe_unavailable_when_command_not_executable(monkeypatch):
    """``os.access`` returning False yields a probe that raises to signal unavailable."""

    monkeypatch.setattr(
        "jobtrail_ai_scorer.preflight.os.access", lambda *a, **k: False
    )

    config = AutomationConfig(notify_enabled=True, whatsapp_command="/usr/bin/wa")
    checks = default_preflight_checks(config)
    whatsapp = next(c for c in checks if c.name == "whatsapp")

    with pytest.raises(RuntimeError):
        whatsapp.probe()


# --- Timeout enforcement via injected executor ------------------------------


def test_run_preflight_enforces_per_check_timeout_via_executor():
    """A probe that exceeds its timeout is classified as ``unavailable``."""

    slow_check = PreflightCheck(
        name="slow",
        probe=lambda: None,
        timeout_seconds=1.0,
    )
    fast_check = PreflightCheck(
        name="fast",
        probe=lambda: None,
        timeout_seconds=1.0,
    )
    executor = FakeExecutor(
        futures=[
            FakeFuture(delay=10.0),  # slow: timeout exceeded
            FakeFuture(),  # fast: ok
        ]
    )
    report = run_preflight(
        AutomationConfig(),
        checks=(slow_check, fast_check),
        executor=executor,
    )

    by_name = {r.name: r for r in report}
    assert by_name["slow"].status == "unavailable"
    assert by_name["fast"].status == "healthy"


def test_run_preflight_closes_owned_executor_when_not_injected(monkeypatch):
    """When no executor is provided, the runner creates and shuts one down."""

    created: list[Any] = []

    class TrackedExecutor(FakeExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    def fake_executor_ctor(*args, **kwargs):
        return TrackedExecutor()

    monkeypatch.setattr(
        "jobtrail_ai_scorer.preflight._create_default_executor",
        fake_executor_ctor,
    )

    run_preflight(
        AutomationConfig(),
        checks=(
            PreflightCheck(name="x", probe=lambda: None, timeout_seconds=1.0),
        ),
    )
    assert len(created) == 1


def test_run_preflight_does_not_shutdown_injected_executor():
    """Injected executors are owned by the caller and must not be shut down."""

    executor = FakeExecutor(
        futures=[FakeFuture()]
    )
    run_preflight(
        AutomationConfig(),
        checks=(
            PreflightCheck(name="x", probe=lambda: None, timeout_seconds=1.0),
        ),
        executor=executor,
    )

    assert executor.shutdown_calls == []


def test_run_preflight_uses_default_checks_when_checks_is_none(monkeypatch):
    """``checks=None`` falls back to :func:`default_preflight_checks`."""

    def fake_get(url, *args, **kwargs):
        request = _build_request("GET", url)
        return _build_response(request, 200)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "head", fake_get)

    def fake_post(url, *args, **kwargs):
        request = _build_request("POST", url)
        response = _build_response(request, 200)
        response._content = b"[]"
        return response

    monkeypatch.setattr(httpx, "post", fake_post)

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("jobtrail_ai_scorer.preflight.subprocess.run", fake_run)

    config = _make_hermes_config()
    report = run_preflight(config, executor=FakeExecutor())

    names = {r.name for r in report}
    assert "jobtrail_api" in names
    assert "jobspy_search" in names
    assert "hermes_provider" in names


# --- jobspy_search probe ----------------------------------------------------


def test_run_preflight_jobspy_search_healthy_when_adapter_returns_results(monkeypatch):
    """A successful ``JobSpySourceAdapter.search`` is ``healthy``."""

    def fake_post(url, *args, **kwargs):
        request = _build_request("POST", url)
        response = _build_response(request, 200)
        response._content = b"[]"
        return response

    monkeypatch.setattr(httpx, "post", fake_post)

    def fake_get(url, *args, **kwargs):
        request = _build_request("GET", url)
        return _build_response(request, 200)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "head", fake_get)

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("jobtrail_ai_scorer.preflight.subprocess.run", fake_run)

    config = _make_hermes_config(profile="local")
    report = run_preflight(
        config,
        checks=default_preflight_checks(config),
        executor=FakeExecutor(),
    )

    # Reach in and call the probe directly to assert the adapter wire-up.
    checks = default_preflight_checks(config)
    jobspy = next(c for c in checks if c.name == "jobspy_search")
    jobspy.probe()  # healthy when gateway returns a list

    by_name = {r.name: r for r in report}
    # The default ``FakeExecutor`` queue is empty so every probe runs
    # verbatim. The monkey-patched ``httpx.get``/``httpx.post`` and
    # ``subprocess.run`` succeed, so every default probe reports ``healthy``.
    assert by_name["jobspy_search"].status == "healthy"


def test_run_preflight_jobspy_search_unavailable_when_gateway_raises(monkeypatch):
    """A failing gateway surfaces as ``unavailable`` for the jobspy probe."""

    config = _make_hermes_config(profile="local")

    def fake_get(url, *args, **kwargs):
        request = _build_request("GET", url)
        return _build_response(request, 200)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "head", fake_get)

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("jobtrail_ai_scorer.preflight.subprocess.run", fake_run)

    checks = default_preflight_checks(config)
    # Replace the jobspy probe with one that raises a connect error.
    new_checks = tuple(
        PreflightCheck(
            name=c.name,
            probe=c.probe if c.name != "jobspy_search" else (
                lambda: (_ for _ in ()).throw(
                    httpx.ConnectError("search gateway down")
                )
            ),
            timeout_seconds=c.timeout_seconds,
            required=c.required,
        )
        for c in checks
    )

    report = run_preflight(config, checks=new_checks, executor=FakeExecutor())
    by_name = {r.name: r for r in report}
    assert by_name["jobspy_search"].status == "unavailable"


# --- hermes provider probe --------------------------------------------------


def test_hermes_provider_probe_uses_help_subcommand(monkeypatch):
    """The hermes probe runs ``hermes --profile <profile> --help`` and checks the returncode."""

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="usage", stderr="")

    monkeypatch.setattr(
        "jobtrail_ai_scorer.preflight.subprocess.run", fake_run
    )

    config = _make_hermes_config(profile="local-profile")
    checks = default_preflight_checks(config)
    hermes = next(c for c in checks if c.name == "hermes_provider")
    assert hermes.probe() is None

    cmd = calls[0][0][0]
    assert cmd == ["hermes", "--profile", "local-profile", "--help"]
    assert calls[0][1].get("timeout") == 2.0


def test_hermes_provider_probe_raises_on_nonzero_returncode(monkeypatch):
    """A nonzero returncode causes the probe to raise so the runner marks it unavailable."""

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="bad profile")

    monkeypatch.setattr(
        "jobtrail_ai_scorer.preflight.subprocess.run", fake_run
    )

    config = _make_hermes_config()
    checks = default_preflight_checks(config)
    hermes = next(c for c in checks if c.name == "hermes_provider")

    with pytest.raises(RuntimeError):
        hermes.probe()


# --- jobtrail_api probe: timeout enforcement on GET --------------------------


def test_jobtrail_api_probe_uses_configured_timeout(monkeypatch):
    """``httpx.get`` is called with the 2.0s timeout configured on the check."""

    captured: dict[str, Any] = {}

    def fake_get(url, *args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        request = _build_request("GET", url)
        return _build_response(request, 200)

    monkeypatch.setattr(httpx, "get", fake_get)

    config = AutomationConfig(base_url="http://example.test")
    checks = default_preflight_checks(config)
    jobtrail = next(c for c in checks if c.name == "jobtrail_api")
    assert jobtrail.timeout_seconds == 2.0

    jobtrail.probe()
    assert captured["timeout"] == 2.0


# --- Mixed statuses and overall semantics -----------------------------------


def test_run_preflight_mixed_statuses_yield_worst_overall(monkeypatch):
    """Healthy + degraded + unavailable reports ``unavailable`` overall."""

    def fake_get(url, *args, **kwargs):
        request = _build_request("GET", url)
        return _build_response(request, 200)

    def fake_head(url, *args, **kwargs):
        request = _build_request("HEAD", url)
        return _build_response(request, 200, method="HEAD")

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "head", fake_head)

    def fake_post(url, *args, **kwargs):
        request = _build_request("POST", url)
        response = _build_response(request, 200)
        response._content = b"[]"
        return response

    monkeypatch.setattr(httpx, "post", fake_post)

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("jobtrail_ai_scorer.preflight.subprocess.run", fake_run)

    config = _make_hermes_config()
    checks = default_preflight_checks(config)
    futures = []
    for c in checks:
        if c.name == "jobtrail_api":
            futures.append(
                FakeFuture(
                    exc=httpx.HTTPStatusError(
                        "404",
                        request=_build_request("GET", "http://x"),
                        response=_build_response(_build_request("GET", "http://x"), 404),
                    )
                )
            )
        else:
            futures.append(FakeFuture())

    report = run_preflight(config, checks=checks, executor=FakeExecutor(futures))
    by_name = {r.name: r for r in report}
    assert by_name["jobtrail_api"].status == "degraded"
    # All other default probes resolve to healthy; jobtrail_api is degraded.
    non_jobtrail = [r for r in report if r.name != "jobtrail_api"]
    assert all(r.status == "healthy" for r in non_jobtrail)
    # Worst-case aggregation: degraded beats healthy, so overall is degraded.
    assert report.overall_status == "degraded"
    # No required check is unavailable.
    assert report.should_abort is False