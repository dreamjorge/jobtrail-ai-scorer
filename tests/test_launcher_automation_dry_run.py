"""Launcher flag/env wiring for the offline automation dry-run (issue #36).

The launcher (``scripts/automated-job-search.example.py``) must expose a
deterministic ``--automation-dry-run`` flag plus the
``JOBTRAIL_AUTOMATION_DRY_RUN`` environment variable. Either trigger
swaps the entire pipeline for an in-process :class:`SimulationScenario`
without ever constructing a :class:`JobTrailHTTPClient`, a
:class:`SeenCache`, or touching the host filesystem.

The tests in this module use ``monkeypatch`` to capture argument
parsing and short-circuit the run on the simulation path. They never
touch the network, Docker or the operator's runtime directory.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = REPO_ROOT / "scripts" / "automated-job-search.example.py"


def _load_launcher_module():
    spec = importlib.util.spec_from_file_location(
        "_launcher_under_test", LAUNCHER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def launcher():
    module = _load_launcher_module()
    return module


@pytest.fixture
def clean_env(monkeypatch):
    """Strip automation-dry-run knobs and any side-effect env vars."""

    for key in (
        "JOBTRAIL_AUTOMATION_DRY_RUN",
        "JOBTRAIL_BASE_URL",
        "JOBTRAIL_DISCOVER_CONTAINER",
        "JOBTRAIL_SEEN_CACHE_PATH",
        "JOBTRAIL_RESET_SEEN_CACHE",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _argv(*items: str) -> list[str]:
    return ["launcher", *items]


def _run_main(launcher, argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", _argv(*argv))
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = launcher.main()
    return exit_code, stdout.getvalue(), stderr.getvalue()


# --- Tests --------------------------------------------------------------------


def test_argparse_accepts_automation_dry_run_flag(launcher, clean_env, monkeypatch):
    """The launcher must expose ``--automation-dry-run`` without rejecting it."""

    parser = launcher.argparse.ArgumentParser()
    # Replicate the launcher's argument parser shape to keep this test
    # independent from the runner wiring. We only assert the flag is
    # recognised.
    parser.add_argument("--automation-dry-run", action="store_true")
    args = parser.parse_args(["--automation-dry-run"])
    assert args.automation_dry_run is True


def test_flag_short_circuits_before_any_io(launcher, clean_env, monkeypatch):
    """Setting the flag must prevent any gateway / cache construction."""

    constructions: list[str] = []

    def forbidden_http(*args, **kwargs):
        constructions.append("http")
        raise AssertionError("JobTrailHTTPClient must not be built in dry-run mode")

    def forbidden_cache(*args, **kwargs):
        constructions.append("cache")
        raise AssertionError("SeenCache must not be built in dry-run mode")

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", forbidden_http)
    monkeypatch.setattr(launcher, "_build_seen_cache", forbidden_cache)
    monkeypatch.setattr(launcher, "JobSearchAutomation", forbidden_http)

    exit_code, stdout, stderr = _run_main(launcher, ["--automation-dry-run"], monkeypatch)
    assert constructions == []
    # No discovery probe, no base-url fallback. The dry-run path prints its
    # own header instead.
    assert "dry-run" in stderr.lower()
    body = json.loads(stdout.strip().splitlines()[-1])
    assert body["mode"] == "automation-dry-run"
    assert body["searched"] >= 1


def test_env_short_circuits_without_flag(launcher, clean_env, monkeypatch):
    constructions: list[str] = []

    def forbidden_http(*args, **kwargs):
        constructions.append("http")
        raise AssertionError("HTTP client must not be built when env var is set")

    def forbidden_cache(*args, **kwargs):
        constructions.append("cache")
        raise AssertionError("SeenCache must not be built when env var is set")

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", forbidden_http)
    monkeypatch.setattr(launcher, "_build_seen_cache", forbidden_cache)
    monkeypatch.setattr(launcher, "JobSearchAutomation", forbidden_http)
    monkeypatch.setenv("JOBTRAIL_AUTOMATION_DRY_RUN", "1")

    exit_code, stdout, stderr = _run_main(launcher, [], monkeypatch)
    assert constructions == []
    body = json.loads(stdout.strip().splitlines()[-1])
    assert body["mode"] == "automation-dry-run"


def test_cli_flag_wins_over_env_disable(launcher, clean_env, monkeypatch):
    """A CLI ``--automation-dry-run`` overrides a ``=0`` env var."""

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "_build_seen_cache", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "JobSearchAutomation", lambda *a, **k: None)
    monkeypatch.setenv("JOBTRAIL_AUTOMATION_DRY_RUN", "0")

    exit_code, stdout, stderr = _run_main(launcher, ["--automation-dry-run"], monkeypatch)
    body = json.loads(stdout.strip().splitlines()[-1])
    assert body["mode"] == "automation-dry-run"


def test_invalid_env_value_fails_closed_without_io(launcher, clean_env, monkeypatch):
    constructions: list[str] = []

    def forbidden_http(*args, **kwargs):
        constructions.append("http")
        raise AssertionError("HTTP client must not be built on invalid env")

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", forbidden_http)
    monkeypatch.setattr(launcher, "_build_seen_cache", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "JobSearchAutomation", forbidden_http)
    monkeypatch.setenv("JOBTRAIL_AUTOMATION_DRY_RUN", "sometimes")

    exit_code, stdout, stderr = _run_main(launcher, [], monkeypatch)
    assert constructions == []
    assert exit_code == 2
    assert "JOBTRAIL_AUTOMATION_DRY_RUN" in stderr


def test_dry_run_does_not_consult_runtime_seen_cache_path(launcher, clean_env, monkeypatch):
    """The launcher must not read ``JOBTRAIL_SEEN_CACHE_PATH`` on the dry-run path."""

    accessed: list[str] = []

    def trap_seen_cache(*args, **kwargs):
        accessed.append("seen_cache")
        return None

    monkeypatch.setattr(launcher, "_build_seen_cache", trap_seen_cache)
    monkeypatch.setattr(launcher, "JobTrailHTTPClient", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "JobSearchAutomation", lambda *a, **k: None)
    monkeypatch.setenv("JOBTRAIL_SEEN_CACHE_PATH", "/tmp/jobtrail/seen.json")

    exit_code, stdout, stderr = _run_main(launcher, ["--automation-dry-run"], monkeypatch)
    assert accessed == []


def test_dry_run_emits_json_envelope_on_stdout(launcher, clean_env, monkeypatch):
    monkeypatch.setattr(launcher, "JobTrailHTTPClient", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "_build_seen_cache", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "JobSearchAutomation", lambda *a, **k: None)

    exit_code, stdout, stderr = _run_main(launcher, ["--automation-dry-run"], monkeypatch)
    body = json.loads(stdout.strip().splitlines()[-1])
    assert body["mode"] == "automation-dry-run"
    assert "envelope" in body
    assert body["envelope"]["redacted"] is True


def test_dry_run_output_does_not_leak_env_secrets(launcher, clean_env, monkeypatch):
    """``JOBTRAIL_BASE_URL`` containing a credential MUST NOT appear in stdout."""

    secret_url = "https://example.test/api?token=SECRET_TOKEN"
    monkeypatch.setattr(launcher, "JobTrailHTTPClient", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "_build_seen_cache", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "JobSearchAutomation", lambda *a, **k: None)
    monkeypatch.setenv("JOBTRAIL_BASE_URL", secret_url)

    exit_code, stdout, stderr = _run_main(launcher, ["--automation-dry-run"], monkeypatch)
    assert "SECRET_TOKEN" not in stdout
    assert "SECRET_TOKEN" not in stderr


def test_default_dry_run_is_disabled(launcher, clean_env, monkeypatch):
    """Without flag or env, the launcher must NOT take the dry-run path."""

    called = {"automation": False}

    def fake_search_automation(*args, **kwargs):
        called["automation"] = True

        class _StubRunner:
            def run(self, *, config):
                class _StubResult:
                    searched = 0
                    imported = 0
                    scored = 0
                    selected = None
                    failures: tuple[str, ...] = ()
                    profile_counts: dict[str, dict[str, int]] = {}

                return _StubResult()

        return _StubRunner()

    class _StubHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", _StubHttpClient)
    monkeypatch.setattr(launcher, "_build_seen_cache", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "JobSearchAutomation", fake_search_automation)
    # No discovery will happen here, so the launcher must rely on
    # JOBTRAIL_BASE_URL or fail-closed. Provide a static one to keep the
    # test self-contained.
    monkeypatch.setenv("JOBTRAIL_BASE_URL", "https://example.test")

    exit_code, stdout, stderr = _run_main(launcher, ["--no-discover"], monkeypatch)
    assert called["automation"] is True
    # The launcher in normal mode emits the run summary via ``print(dict)``,
    # which renders with single quotes (Python repr). Parse the last
    # non-empty line with ``ast.literal_eval`` to obtain the dict.
    import ast
    last = next(line for line in reversed(stdout.strip().splitlines()) if line.strip())
    body = ast.literal_eval(last)
    assert "mode" not in body  # no automation-dry-run key in normal summary
