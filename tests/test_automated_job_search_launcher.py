"""Tests for the discovery container resolution in the automated launcher.

``scripts/automated-job-search.example.py`` is not part of the installed
package (hyphenated filename), so it is loaded by path like the other
``scripts/*.py`` helpers tested elsewhere (see ``tests/test_runtime_backup.py``).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
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


def test_launcher_passes_configured_ats_boards_to_automation(
    monkeypatch, launcher
) -> None:
    captured: dict[str, object] = {}

    class FakeGateway:
        def __init__(self, base_url):
            self.base_url = base_url

        def close(self):
            pass

    class FakeAutomation:
        def __init__(self, gateway, *, seen_cache=None, ats_boards=None):
            captured["ats_boards"] = ats_boards

        def run(self, *, config):
            return SimpleNamespace(
                searched=0,
                imported=0,
                scored=0,
                selected=None,
                failures=(),
                profile_counts={},
            )

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", FakeGateway)
    monkeypatch.setattr(launcher, "JobSearchAutomation", FakeAutomation)
    monkeypatch.setattr(launcher, "_build_seen_cache", lambda args: None)
    monkeypatch.setenv(
        "JOB_ATS_BOARDS", '{"lever_boards":["acme"],"results_wanted":15}'
    )
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(
        sys,
        "argv",
        ["automated-job-search.example.py", "--no-discover"],
    )

    assert launcher.main() == 0
    assert captured["ats_boards"].lever_boards == ("acme",)
    assert captured["ats_boards"].results_wanted == 15


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


def test_dry_run_flag_overrides_env_and_skips_seen_cache(
    monkeypatch, launcher
) -> None:
    captured: dict[str, object] = {}

    class FakeGateway:
        def __init__(self, base_url):
            self.base_url = base_url

        def close(self):
            pass

    class FakeAutomation:
        def __init__(self, gateway, *, seen_cache=None, ats_boards=None):
            captured["seen_cache"] = seen_cache

        def run(self, *, config):
            captured["dry_run"] = config.dry_run
            return SimpleNamespace(
                searched=0,
                imported=0,
                scored=0,
                selected=None,
                failures=(),
                profile_counts={},
                dry_run=True,
                planned_operations={},
                notification_preview=None,
            )

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", FakeGateway)
    monkeypatch.setattr(launcher, "JobSearchAutomation", FakeAutomation)
    def unexpected_cache_build(_args):
        raise AssertionError("dry-run must not construct SeenCache")
    monkeypatch.setattr(launcher, "_build_seen_cache", unexpected_cache_build)
    monkeypatch.setenv("JOBTRAIL_AUTOMATION_DRY_RUN", "0")
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(
        sys,
        "argv",
        ["automated-job-search.example.py", "--no-discover", "--dry-run"],
    )

    assert launcher.main() == 0
    assert captured == {"seen_cache": None, "dry_run": True}


def test_dry_run_env_propagates_without_flag(monkeypatch, launcher) -> None:
    captured: dict[str, object] = {}

    class FakeGateway:
        def __init__(self, base_url):
            self.base_url = base_url

        def close(self):
            pass

    class FakeAutomation:
        def __init__(self, gateway, *, seen_cache=None, ats_boards=None):
            captured["seen_cache"] = seen_cache

        def run(self, *, config):
            captured["dry_run"] = config.dry_run
            return SimpleNamespace(
                searched=0,
                imported=0,
                scored=0,
                selected=None,
                failures=(),
                profile_counts={},
                dry_run=True,
                planned_operations={},
                notification_preview=None,
            )

    monkeypatch.setattr(launcher, "JobTrailHTTPClient", FakeGateway)
    monkeypatch.setattr(launcher, "JobSearchAutomation", FakeAutomation)
    monkeypatch.setattr(
        launcher,
        "_build_seen_cache",
        lambda _args: (_ for _ in ()).throw(AssertionError("cache constructed")),
    )
    monkeypatch.setenv("JOBTRAIL_AUTOMATION_DRY_RUN", "1")
    monkeypatch.setenv("SCORER_CONFIG_PATH", "safe/config.yaml")
    monkeypatch.setattr(
        sys, "argv", ["automated-job-search.example.py", "--no-discover"]
    )

    assert launcher.main() == 0
    assert captured == {"seen_cache": None, "dry_run": True}


def _copy_notify_helper(tmp_path: Path) -> Path:
    helper = tmp_path / "notify-whatsapp-via-hermes.example.sh"
    shutil.copy2(ROOT / "scripts/notify-whatsapp-via-hermes.example.sh", helper)
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    return helper


def test_whatsapp_launcher_direct_sends_rendered_stdin_unchanged(tmp_path) -> None:
    helper = _copy_notify_helper(tmp_path)
    fake_hermes = tmp_path / "fake-hermes"
    args_path = tmp_path / "args.txt"
    body_path = tmp_path / "body.txt"
    fake_hermes.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_ARGS_PATH\"\n"
        "cat > \"$FAKE_BODY_PATH\"\n"
    )
    fake_hermes.chmod(fake_hermes.stat().st_mode | stat.S_IXUSR)
    rendered = "#1 Python Engineer\nVer publicación: https://jobs.test/1\n"
    env = {
        **os.environ,
        "WHATSAPP_NOTIFY_ENABLED": "1",
        "HERMES_EXECUTABLE": str(fake_hermes),
        "FAKE_ARGS_PATH": str(args_path),
        "FAKE_BODY_PATH": str(body_path),
        "HERMES_WHATSAPP_TARGET": "whatsapp:Test Recipient",
    }

    result = subprocess.run(
        [str(helper)], input=rendered, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )

    assert result.returncode == 0
    assert args_path.read_text().splitlines() == [
        "send", "--to", "whatsapp:Test Recipient"
    ]
    assert body_path.read_text() == rendered


def test_whatsapp_launcher_requires_explicit_target(tmp_path) -> None:
    helper = _copy_notify_helper(tmp_path)
    fake_hermes = tmp_path / "fake-hermes"
    args_path = tmp_path / "args.txt"
    body_path = tmp_path / "body.txt"
    fake_hermes.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_ARGS_PATH\"\n"
        "cat > \"$FAKE_BODY_PATH\"\n"
    )
    fake_hermes.chmod(fake_hermes.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "WHATSAPP_NOTIFY_ENABLED": "1",
        "HERMES_EXECUTABLE": str(fake_hermes),
        "FAKE_ARGS_PATH": str(args_path),
        "FAKE_BODY_PATH": str(body_path),
    }
    rendered = "2 new matches; log: /tmp/jobtrail.log"

    result = subprocess.run(
        [str(helper), rendered], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )

    assert result.returncode == 2
    assert "HERMES_WHATSAPP_TARGET" in result.stderr
    assert "non-empty" in result.stderr
    assert not args_path.exists()
    assert not body_path.exists()


def test_whatsapp_launcher_empty_input_fails_without_invoking_hermes(tmp_path) -> None:
    helper = _copy_notify_helper(tmp_path)
    marker = tmp_path / "called"
    fake_hermes = tmp_path / "fake-hermes"
    fake_hermes.write_text(f"#!/usr/bin/env bash\ntouch {marker}\n")
    fake_hermes.chmod(fake_hermes.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        [str(helper)], input="  \n", env={
            **os.environ,
            "WHATSAPP_NOTIFY_ENABLED": "1",
            "HERMES_EXECUTABLE": str(fake_hermes),
        }, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )

    assert result.returncode == 2
    assert not marker.exists()


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
