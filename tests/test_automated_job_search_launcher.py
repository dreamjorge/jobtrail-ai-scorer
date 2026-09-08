"""Tests for the discovery container resolution in the automated launcher.

``scripts/automated-job-search.example.py`` is not part of the installed
package (hyphenated filename), so it is loaded by path like the other
``scripts/*.py`` helpers tested elsewhere (see ``tests/test_runtime_backup.py``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "automated-job-search.example.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "automated_job_search_example", LAUNCHER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def launcher():
    return _load_module()


class _StopAfterCapture(Exception):
    """Raised once the discovery call args are captured, to short-circuit main()."""


def test_final_output_includes_profile_counts(monkeypatch, capsys, launcher) -> None:
    class FakeGateway:
        def __init__(self, base_url):
            self.base_url = base_url

        def close(self):
            pass

    class FakeAutomation:
        def __init__(self, gateway, *, seen_cache=None, ats_boards=None):
            self.gateway = gateway
            self.seen_cache = seen_cache

        def run(self, *, config):
            return SimpleNamespace(
                searched=1,
                imported=1,
                scored=1,
                selected={"title": "Python Engineer"},
                failures=(),
                profile_counts={
                    "python": {
                        "searched": 1,
                        "imported": 1,
                        "duplicates": 0,
                        "failures": 0,
                    }
                },
            )

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", FakeGateway)
    monkeypatch.setattr(launcher, "JobSearchAutomation", FakeAutomation)
    monkeypatch.setattr(launcher, "_build_seen_cache", lambda args: None)
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(
        sys,
        "argv",
        ["automated-job-search.example.py", "--no-discover"],
    )

    assert launcher.main() == 0
    output = capsys.readouterr().out
    assert "'profile_counts': {'python':" in output


def test_container_env_var_is_honored_when_flag_omitted(
    monkeypatch, launcher
) -> None:
    """JOBTRAIL_DISCOVER_CONTAINER must not be clobbered by argparse's default.

    Regression: --container previously defaulted to DISCOVER_DEFAULT_CONTAINER
    unconditionally, so omitting the flag always discarded an operator's
    JOBTRAIL_DISCOVER_CONTAINER in favor of "jobtrail-backend-1".
    """

    captured: dict[str, object] = {}

    def fake_resolve(config, *, container_name=None, **kwargs):
        captured["container_name"] = container_name
        captured["config_discover_container"] = config.discover_container
        raise _StopAfterCapture

    monkeypatch.setattr(launcher, "resolve_automation_base_url", fake_resolve)
    monkeypatch.setenv("JOBTRAIL_DISCOVER_CONTAINER", "my-custom-container")
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(sys, "argv", ["automated-job-search.example.py"])

    with pytest.raises(_StopAfterCapture):
        launcher.main()

    assert captured["container_name"] == "my-custom-container"
    assert captured["config_discover_container"] == "my-custom-container"


def test_container_flag_overrides_env_var(monkeypatch, launcher) -> None:
    """An explicit --container must still win over JOBTRAIL_DISCOVER_CONTAINER."""

    captured: dict[str, object] = {}

    def fake_resolve(config, *, container_name=None, **kwargs):
        captured["container_name"] = container_name
        raise _StopAfterCapture

    monkeypatch.setattr(launcher, "resolve_automation_base_url", fake_resolve)
    monkeypatch.setenv("JOBTRAIL_DISCOVER_CONTAINER", "env-container")
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(
        sys,
        "argv",
        ["automated-job-search.example.py", "--container", "cli-container"],
    )

    with pytest.raises(_StopAfterCapture):
        launcher.main()

    assert captured["container_name"] == "cli-container"


def test_container_defaults_when_neither_flag_nor_env_set(
    monkeypatch, launcher
) -> None:
    captured: dict[str, object] = {}

    def fake_resolve(config, *, container_name=None, **kwargs):
        captured["container_name"] = container_name
        raise _StopAfterCapture

    monkeypatch.setattr(launcher, "resolve_automation_base_url", fake_resolve)
    monkeypatch.delenv("JOBTRAIL_DISCOVER_CONTAINER", raising=False)
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(sys, "argv", ["automated-job-search.example.py"])

    with pytest.raises(_StopAfterCapture):
        launcher.main()

    assert captured["container_name"] == launcher.DISCOVER_DEFAULT_CONTAINER
