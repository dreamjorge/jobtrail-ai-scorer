# Hermes Runtime Automation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add safe example automation for running JobTrail AI scorer against the existing Hermes Docker/WhatsApp runtime without committing private config or secrets.

**Architecture:** Keep Python scoring logic separate from runtime orchestration. Add example shell helpers and documentation that operators copy into ignored local files, then configure with local paths and environment variables. WhatsApp notifications are routed through Hermes `job-search` and carry summaries only.

**Tech Stack:** Python 3.11, pytest, ruff, POSIX shell, Docker CLI, Hermes CLI.

---

### Task 1: Add packaging coverage for automation examples

**Files:**
- Modify: `tests/test_packaging.py`
- Create later: `scripts/hermes-docker-wrapper.example.sh`
- Create later: `scripts/run-scorer.example.sh`
- Create later: `scripts/notify-whatsapp-via-hermes.example.sh`
- Create later: `docs/runtime-automation.md`

**Step 1: Write the failing test**

Add a test asserting the expected automation example files exist and that executable shell examples start with a bash shebang.

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_packaging.py::test_runtime_automation_examples_are_packaged`

Expected: FAIL because the files do not exist yet.

**Step 3: Implement only empty/minimal placeholder files if needed**

Create the target files with a shebang for scripts and minimal docs heading. This task establishes packaging surfaces only.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_packaging.py::test_runtime_automation_examples_are_packaged`

Expected: PASS.

---

### Task 2: Add Hermes Docker wrapper example

**Files:**
- Modify: `scripts/hermes-docker-wrapper.example.sh`
- Test: `tests/test_packaging.py` or a new script-content test if needed

**Step 1: Write the failing test**

Assert the wrapper contains:
- `set -euo pipefail`
- configurable `HERMES_CONTAINER`
- configurable `HERMES_BIN`
- `exec docker exec "$HERMES_CONTAINER" "$HERMES_BIN" "$@"`
- no `docker compose down`, `docker system prune`, or `docker volume prune`.

**Step 2: Run test to verify it fails**

Run the targeted test.

**Step 3: Implement wrapper**

Write a copyable example script:

```bash
#!/usr/bin/env bash
set -euo pipefail

HERMES_CONTAINER="${HERMES_CONTAINER:-hermes}"
HERMES_BIN="${HERMES_BIN:-/opt/hermes/.venv/bin/hermes}"

exec docker exec "$HERMES_CONTAINER" "$HERMES_BIN" "$@"
```

**Step 4: Run test to verify it passes**

Run targeted test, then keep full verification for the final task.

---

### Task 3: Add scorer runner example

**Files:**
- Modify: `scripts/run-scorer.example.sh`
- Test: `tests/test_packaging.py` or script-content test

**Step 1: Write the failing test**

Assert the runner:
- requires `SCORER_CONFIG_PATH`
- supports `SCORER_LIMIT`, defaulting to `1`
- supports `SCORER_DRY_RUN`, defaulting to `1`
- calls `jobtrail-ai-scorer score --config "$SCORER_CONFIG_PATH" --limit "$SCORER_LIMIT"`
- adds `--dry-run` when dry-run is enabled
- writes logs to a configurable path outside the repository by default
- does not contain destructive Docker commands.

**Step 2: Run test to verify it fails**

Run targeted test.

**Step 3: Implement runner**

Create a shell example that operators can copy and customize. It must preserve the scorer exit code and print the log path.

**Step 4: Run test to verify it passes**

Run targeted test.

---

### Task 4: Add optional WhatsApp notification helper through Hermes

**Files:**
- Modify: `scripts/notify-whatsapp-via-hermes.example.sh`
- Test: `tests/test_packaging.py` or script-content test

**Step 1: Write the failing test**

Assert the helper:
- requires an explicit `WHATSAPP_NOTIFY_ENABLED=1`
- uses `HERMES_PROFILE`, defaulting to `job-search`
- invokes Hermes with `--profile "$HERMES_PROFILE" -z "$PROMPT" --cli`
- sends only summary/log metadata, not raw job descriptions or candidate profile content
- does not contain destructive Docker commands.

**Step 2: Run test to verify it fails**

Run targeted test.

**Step 3: Implement helper**

Create a script that accepts summary text as arguments/stdin, builds a short prompt requesting WhatsApp delivery through connected Hermes tools, and exits nonzero if Hermes fails.

**Step 4: Run test to verify it passes**

Run targeted test.

---

### Task 5: Document runtime automation

**Files:**
- Modify: `docs/runtime-automation.md`
- Modify: `README.md`
- Test: `tests/test_packaging.py` or docs-content test

**Step 1: Write the failing test**

Assert docs mention:
- dry-run first
- local config stays ignored
- both JobTrail compose files must be used for JobTrail maintenance commands
- never run `docker compose down -v`
- Hermes Docker wrapper
- WhatsApp via Hermes `job-search`
- summaries only, no raw prompts/profile/descriptions

**Step 2: Run test to verify it fails**

Run targeted test.

**Step 3: Implement docs**

Document copy/setup steps, local config example, systemd/cron recommendation, and WhatsApp behavior.

**Step 4: Run test to verify it passes**

Run targeted test.

---

### Task 6: Final verification

**Files:**
- Verify all changed files

**Step 1: Run focused tests**

Run any targeted tests added in previous tasks.

**Step 2: Run full test suite**

Run: `pytest -q`

Expected: all tests pass.

**Step 3: Run linter**

Run: `ruff check src tests`

Expected: all checks pass.

**Step 4: Manual dry-run smoke**

If local temporary config is still available, run the scorer with `--dry-run --force --limit 1`. Do not send WhatsApp unless explicitly authorized.

**Step 5: Review**

Request independent normal review of scripts/docs and report findings before asking about commit/PR.
