import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def test_distribution_metadata_and_runtime_files_exist():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "jobtrail-ai-scorer = \"jobtrail_ai_scorer.main:app\"" in pyproject
    assert (ROOT / "README.md").is_file()
    assert (ROOT / "LICENSE").is_file()
    assert (ROOT / "docker-compose.yml").is_file()
    assert (ROOT / "Dockerfile").is_file()
    assert (ROOT / "candidate-profile.example.md").is_file()


def test_runtime_automation_examples_are_packaged():
    scripts = [
        ROOT / "scripts/hermes-docker-wrapper.example.sh",
        ROOT / "scripts/legacy/run-scorer.example.sh",
        ROOT / "scripts/notify-whatsapp-via-hermes.example.sh",
    ]
    for script in scripts:
        assert script.is_file()
        assert script.read_text().startswith("#!/usr/bin/env bash")

    assert (ROOT / "docs/runtime-automation.md").is_file()


def test_hermes_docker_wrapper_uses_safe_configurable_exec():
    script = (ROOT / "scripts/hermes-docker-wrapper.example.sh").read_text()

    assert "set -euo pipefail" in script
    assert 'HERMES_CONTAINER="${HERMES_CONTAINER:-hermes}"' in script
    assert 'HERMES_BIN="${HERMES_BIN:-/opt/hermes/.venv/bin/hermes}"' in script
    assert 'exec docker exec "$HERMES_CONTAINER" "$HERMES_BIN" "$@"' in script

    destructive_patterns = [
        "docker compose down",
        "docker system prune",
        "docker volume prune",
        "down -v",
    ]
    for pattern in destructive_patterns:
        assert pattern not in script


def test_run_scorer_example_uses_safe_configurable_runner():
    script = (ROOT / "scripts/legacy/run-scorer.example.sh").read_text()

    assert "set -euo pipefail" in script
    assert 'SCORER_CONFIG_PATH must be set' in script
    assert '[[ -f "$SCORER_CONFIG_PATH" ]]' in script
    assert 'SCORER_COMMAND="${SCORER_COMMAND:-jobtrail-ai-scorer}"' in script
    assert 'SCORER_LIMIT="${SCORER_LIMIT:-1}"' in script
    assert 'SCORER_DRY_RUN="${SCORER_DRY_RUN:-1}"' in script
    assert 'SCORER_LOG_DIR="${SCORER_LOG_DIR:-/tmp/jobtrail-ai-scorer-logs}"' in script
    assert 'mkdir -p "$SCORER_LOG_DIR"' in script
    assert '"$SCORER_COMMAND" score --config "$SCORER_CONFIG_PATH" --limit "$SCORER_LIMIT"' in script
    assert '"--dry-run"' in script
    assert "eval " not in script
    assert 'PIPESTATUS[0]' in script
    assert 'exit "$scorer_exit"' in script
    assert 'Log written to: $log_path' in script

    destructive_patterns = [
        "docker compose down",
        "docker system prune",
        "docker volume prune",
        "down -v",
    ]
    for pattern in destructive_patterns:
        assert pattern not in script


@pytest.mark.parametrize(
    ("dry_run", "expected_args"),
    [
        (
            "1",
            [
                "score",
                "--config",
                "{config}",
                "--limit",
                "1",
                "--dry-run",
            ],
        ),
        (
            "0",
            [
                "score",
                "--config",
                "{config}",
                "--limit",
                "1",
            ],
        ),
    ],
)
def test_run_scorer_example_preserves_scorer_exit_code(dry_run, expected_args, tmp_path):
    runner = tmp_path / "run-scorer.example.sh"
    shutil.copy2(ROOT / "scripts/legacy/run-scorer.example.sh", runner)
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "config.toml"
    config.write_text("[scorer]\n")
    log_dir = tmp_path / "logs"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_scorer = bin_dir / "jobtrail-ai-scorer"
    fake_scorer.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_SCORER_ARGS_PATH\"\n"
        "echo 'fake scorer failed'\n"
        "exit 37\n"
    )
    fake_scorer.chmod(fake_scorer.stat().st_mode | stat.S_IXUSR)
    args_path = tmp_path / "scorer-args.txt"

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SCORER_CONFIG_PATH": str(config),
        "SCORER_DRY_RUN": dry_run,
        "SCORER_LOG_DIR": str(log_dir),
        "FAKE_SCORER_ARGS_PATH": str(args_path),
    }

    result = subprocess.run(
        [str(runner)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 37
    assert "fake scorer failed" in result.stdout
    assert "Scorer failed with exit code 37" in result.stderr
    assert args_path.read_text().splitlines() == [
        str(config) if arg == "{config}" else arg for arg in expected_args
    ]


def test_run_scorer_example_uses_configured_scorer_command_without_path_fallback(tmp_path):
    runner = tmp_path / "run-scorer.example.sh"
    shutil.copy2(ROOT / "scripts/legacy/run-scorer.example.sh", runner)
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "config.toml"
    config.write_text("[scorer]\n")
    log_dir = tmp_path / "logs"
    path_bin_dir = tmp_path / "path-bin"
    path_bin_dir.mkdir()
    custom_scorer = tmp_path / "custom-scorer"
    args_path = tmp_path / "custom-scorer-args.txt"
    path_fallback_marker = tmp_path / "path-fallback-was-called"

    path_scorer = path_bin_dir / "jobtrail-ai-scorer"
    path_scorer.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {path_fallback_marker}\n"
        "exit 99\n"
    )
    path_scorer.chmod(path_scorer.stat().st_mode | stat.S_IXUSR)

    custom_scorer.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_SCORER_ARGS_PATH\"\n"
        "exit 0\n"
    )
    custom_scorer.chmod(custom_scorer.stat().st_mode | stat.S_IXUSR)

    env = {
        **os.environ,
        "PATH": f"{path_bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SCORER_COMMAND": str(custom_scorer),
        "SCORER_CONFIG_PATH": str(config),
        "SCORER_DRY_RUN": "1",
        "SCORER_LOG_DIR": str(log_dir),
        "FAKE_SCORER_ARGS_PATH": str(args_path),
    }

    result = subprocess.run(
        [str(runner)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert not path_fallback_marker.exists()
    assert args_path.read_text().splitlines() == [
        "score",
        "--config",
        str(config),
        "--limit",
        "1",
        "--dry-run",
    ]


def test_run_scorer_example_rejects_invalid_dry_run_without_invoking_scorer(tmp_path):
    runner = tmp_path / "run-scorer.example.sh"
    shutil.copy2(ROOT / "scripts/legacy/run-scorer.example.sh", runner)
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "config.toml"
    config.write_text("[scorer]\n")
    log_dir = tmp_path / "logs"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "scorer-was-called"
    fake_scorer = bin_dir / "jobtrail-ai-scorer"
    fake_scorer.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {marker}\n"
        "exit 99\n"
    )
    fake_scorer.chmod(fake_scorer.stat().st_mode | stat.S_IXUSR)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SCORER_CONFIG_PATH": str(config),
        "SCORER_DRY_RUN": "TRUE",
        "SCORER_LOG_DIR": str(log_dir),
    }

    result = subprocess.run(
        [str(runner)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert "SCORER_DRY_RUN" in result.stderr
    assert "accepted values" in result.stderr
    assert "1, true, yes" in result.stderr
    assert "0, false, no" in result.stderr


def test_run_scorer_example_zero_invokes_scorer_without_dry_run(tmp_path):
    runner = tmp_path / "run-scorer.example.sh"
    shutil.copy2(ROOT / "scripts/legacy/run-scorer.example.sh", runner)
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR)

    config = tmp_path / "config.toml"
    config.write_text("[scorer]\n")
    log_dir = tmp_path / "logs"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_path = tmp_path / "scorer-args.txt"
    fake_scorer = bin_dir / "jobtrail-ai-scorer"
    fake_scorer.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_SCORER_ARGS_PATH\"\n"
        "exit 0\n"
    )
    fake_scorer.chmod(fake_scorer.stat().st_mode | stat.S_IXUSR)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SCORER_CONFIG_PATH": str(config),
        "SCORER_DRY_RUN": "0",
        "SCORER_LOG_DIR": str(log_dir),
        "FAKE_SCORER_ARGS_PATH": str(args_path),
    }

    result = subprocess.run(
        [str(runner)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert args_path.read_text().splitlines() == [
        "score",
        "--config",
        str(config),
        "--limit",
        "1",
    ]
    assert "--dry-run" not in args_path.read_text().splitlines()


def test_run_scorer_example_captures_pipeline_status_immediately():
    lines = (ROOT / "scripts/legacy/run-scorer.example.sh").read_text().splitlines()
    pipeline_lines = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith('"$SCORER_COMMAND" ') and "| tee" in line
    ]

    assert len(pipeline_lines) == 2
    for index in pipeline_lines:
        assert lines[index + 1].strip() == 'scorer_exit="${PIPESTATUS[0]}"'


def test_whatsapp_notification_helper_requires_safe_hermes_target():
    script = (ROOT / "scripts/notify-whatsapp-via-hermes.example.sh").read_text()

    assert "set -euo pipefail" in script
    assert 'WHATSAPP_NOTIFY_ENABLED" != "1"' in script
    assert 'HERMES_WHATSAPP_TARGET="${HERMES_WHATSAPP_TARGET:-}"' in script
    assert "HERMES_WHATSAPP_TARGET must be set to a non-empty local recipient" in script
    assert "whatsapp:" not in script
    assert 'HERMES_EXECUTABLE="${HERMES_EXECUTABLE:-hermes}"' in script
    assert 'printf \'%s\' "$SUMMARY_TEXT" | "$HERMES_EXECUTABLE" send --to "$HERMES_WHATSAPP_TARGET"' in script
    assert "summary" in script.lower()

    forbidden_sensitive_inputs = [
        "JOB_DESCRIPTION",
        "JOB_DESCRIPTIONS",
        "CANDIDATE_PROFILE",
        "RAW_PROMPT",
        "RAW_PROMPTS",
        "NOTES_PATH",
        "NOTES_FILE",
        "TOKEN",
        "SECRET",
    ]
    for forbidden in forbidden_sensitive_inputs:
        assert forbidden not in script

    destructive_patterns = [
        "docker compose down",
        "docker system prune",
        "docker volume prune",
        "down -v",
    ]
    for pattern in destructive_patterns:
        assert pattern not in script


def test_whatsapp_notification_helper_disabled_does_not_invoke_hermes(tmp_path):
    helper = tmp_path / "notify-whatsapp-via-hermes.example.sh"
    shutil.copy2(ROOT / "scripts/notify-whatsapp-via-hermes.example.sh", helper)
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)

    fake_hermes = tmp_path / "fake-hermes"
    marker = tmp_path / "hermes-was-called"
    fake_hermes.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {marker}\n"
        "exit 99\n"
    )
    fake_hermes.chmod(fake_hermes.stat().st_mode | stat.S_IXUSR)

    env = {
        **os.environ,
        "HERMES_EXECUTABLE": str(fake_hermes),
    }
    env.pop("WHATSAPP_NOTIFY_ENABLED", None)

    result = subprocess.run(
        [str(helper), "2 new matches", "log: /tmp/jobtrail.log"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert not marker.exists()


def test_whatsapp_notification_helper_sends_summary_directly_to_configured_target(tmp_path):
    helper = tmp_path / "notify-whatsapp-via-hermes.example.sh"
    shutil.copy2(ROOT / "scripts/notify-whatsapp-via-hermes.example.sh", helper)
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)

    fake_hermes = tmp_path / "fake-hermes"
    args_path = tmp_path / "hermes-args.txt"
    body_path = tmp_path / "hermes-body.txt"
    fake_hermes.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_HERMES_ARGS_PATH\"\n"
        "cat > \"$FAKE_HERMES_BODY_PATH\"\n"
        "exit 0\n"
    )
    fake_hermes.chmod(fake_hermes.stat().st_mode | stat.S_IXUSR)

    env = {
        **os.environ,
        "WHATSAPP_NOTIFY_ENABLED": "1",
        "HERMES_EXECUTABLE": str(fake_hermes),
        "HERMES_WHATSAPP_TARGET": "whatsapp:Test Recipient",
        "FAKE_HERMES_ARGS_PATH": str(args_path),
        "FAKE_HERMES_BODY_PATH": str(body_path),
    }

    result = subprocess.run(
        [str(helper), "2 new matches", "log: /tmp/jobtrail.log"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    args = args_path.read_text().splitlines()
    assert args == ["send", "--to", "whatsapp:Test Recipient"]
    prompt = body_path.read_text()
    assert "2 new matches" in prompt
    assert "log: /tmp/jobtrail.log" in prompt
    assert "WhatsApp" not in prompt
    for sensitive_phrase in [
        "job description:",
        "candidate profile:",
        "raw prompt:",
        "notes:",
        "token:",
    ]:
        assert sensitive_phrase not in prompt.lower()

def test_runtime_automation_docs_cover_safe_operations():
    docs = (ROOT / "docs/runtime-automation.md").read_text().lower()

    required_phrases = [
        "dry-run first",
        "config.yaml",
        "candidate profiles",
        "logs",
        "secrets",
        "do not commit",
        "compose.hub.yml",
        "compose.override.yml",
        "docker compose down -v",
        "hermes-docker-wrapper.example.sh",
        "job-search",
        "summaries only",
        "raw prompts",
        "candidate profile",
        "job descriptions",
        "notes",
        "tokens",
        "cron",
        "systemd timer",
        "openclaw cron",
    ]
    for phrase in required_phrases:
        assert phrase in docs

    assert "never run `docker compose down -v`" in docs
    assert "both" in docs and "jobtrail" in docs and "maintenance commands" in docs


def test_readme_links_to_runtime_automation_docs():
    readme = (ROOT / "README.md").read_text()

    assert "docs/runtime-automation.md" in readme
    assert "[Runtime automation](docs/runtime-automation.md)" in readme
