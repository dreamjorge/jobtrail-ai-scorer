# WhatsApp Detailed Card Formatting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace flat/raw WhatsApp summaries with bounded, readable detailed cards while preserving one-message delivery, privacy, deterministic ordering, and dry-run safety.

**Architecture:** Keep selection and `NotificationBuilder` unchanged as the privacy boundary, then render only its bounded summaries through a deterministic card formatter. Update the repository notification launcher to send rendered text through Hermes direct `send`, using the configured target rather than an LLM-dependent formatting loop. Keep automation responsible for one notifier call and keep deployment installation synchronized with the repository launcher.

**Tech Stack:** Python 3.11, pytest, Bash, Hermes CLI, existing JobTrail `NotificationBuilder` and automation/e2e test fakes.

---

### Task 1: Map current notification and deployment surfaces

**Files:**
- Read: `src/jobtrail_ai_scorer/automation.py`
- Read: `src/jobtrail_ai_scorer/notify.py`
- Read: `scripts/notify-whatsapp-via-hermes.example.sh`
- Read: `scripts/runtime_install.py`
- Read: `tests/test_automation.py`
- Read: `tests/test_automation_e2e.py`

**Step 1: Confirm existing contracts**

Record the current function that builds notification payloads, the notifier callback boundary, the dry-run preview shape, the installed launcher source, and the configured Hermes environment variables. Do not change code.

**Step 2: Confirm baseline**

Run:

```bash
python -m pytest -q
python -m pytest -q -m e2e
```

Expected: existing suite passes before implementation.

**Step 3: Commit**

No commit is needed for this read-only task.

---

### Task 2: Add failing formatter tests

**Files:**
- Modify: `tests/test_automation.py` or the existing notification-focused test module
- Modify: `tests/test_automation_e2e.py`

**Step 1: Write failing single-card test**

Add a test that passes one already-built bounded summary to the formatter and asserts:

- title/company/location are grouped in the header;
- score and normalized recommendation are prominent;
- strengths and gaps render as short bullet lists;
- links are separate labeled lines;
- no JSON braces or raw technical fields appear.

**Step 2: Write failing digest-card test**

Add a test for three summaries asserting numbered cards, deterministic order, visible separators, and one final message string.

**Step 3: Write failing omission/privacy tests**

Cover empty strengths/gaps, missing links, forbidden-token values, long strings, and unexpected fields. Assert that output remains bounded and contains no CV/profile/prompt/credential/description/notes content.

**Step 4: Run focused tests**

Run:

```bash
python -m pytest tests/test_automation.py tests/test_automation_e2e.py -q
```

Expected: new formatter tests fail because the formatter does not exist or does not yet produce the card contract.

---

### Task 3: Implement deterministic card rendering

**Files:**
- Modify: `src/jobtrail_ai_scorer/automation.py` (or the existing notification-rendering module if Task 1 confirms a better existing boundary)
- Test: `tests/test_automation.py`
- Test: `tests/test_automation_e2e.py`

**Step 1: Implement one-card rendering**

Create a renderer that accepts only `NotificationBuilder` output. Render a compact header, score/recommendation line, identity line, optional strengths/gaps bullets, and labeled links. Use the existing clipped/scrubbed fields without reading source jobs or scores directly.

**Step 2: Implement digest rendering**

Render a single card without a redundant rank for N=1. For N>1, add the selector-provided rank and join cards with a stable separator. Preserve the existing top-N order; do not sort again in the renderer.

**Step 3: Keep the notifier contract**

Ensure normal automation still calls the notifier exactly once and passes the complete rendered digest. Ensure dry-run still records a preview and never calls the notifier.

**Step 4: Run focused tests**

Run:

```bash
python -m pytest tests/test_automation.py tests/test_automation_e2e.py -q
```

Expected: all focused formatter, ordering, redaction, one-call, and dry-run tests pass.

**Step 5: Commit**

```bash
git add src/jobtrail_ai_scorer/automation.py tests/test_automation.py tests/test_automation_e2e.py
git commit -m "feat(notify): render detailed WhatsApp cards"
```

---

### Task 4: Replace LLM-dependent launcher formatting

**Files:**
- Modify: `scripts/notify-whatsapp-via-hermes.example.sh`
- Modify: `scripts/runtime_install.py` if it embeds or copies the launcher
- Test: `tests/test_automated_job_search_launcher.py` or an appropriate shell/launcher test

**Step 1: Write failing launcher test**

Mock the Hermes executable and assert the launcher invokes direct `hermes send` with the configured WhatsApp target, passes the rendered text unchanged, and does not invoke `hermes -z`.

**Step 2: Implement direct-send launcher**

Read the already-rendered message from stdin/arguments, validate it is non-empty, resolve the explicit target from deployment configuration, and call Hermes direct `send`. Keep `set -euo pipefail`, avoid temporary files, and never pass private source material or prompts.

**Step 3: Run launcher tests**

Run:

```bash
python -m pytest tests/test_automated_job_search_launcher.py -q
bash -n scripts/notify-whatsapp-via-hermes.example.sh
```

Expected: tests pass and shell syntax is valid.

**Step 4: Commit**

```bash
git add scripts/notify-whatsapp-via-hermes.example.sh scripts/runtime_install.py tests/test_automated_job_search_launcher.py
git commit -m "fix(notify): send rendered summaries directly"
```

---

### Task 5: Update documentation and deployment contract

**Files:**
- Modify: `docs/runtime-automation.md`
- Modify: `scripts/automated-job-search.example.py` if notification formatting/config is documented there
- Test: relevant documentation/config tests if present

**Step 1: Document message format**

Explain the detailed-card layout, the single-message invariant, bounded fields, and the fact that Hermes direct send no longer requires an LLM formatting response.

**Step 2: Document target configuration**

Document the explicit WhatsApp target variable/name required by the launcher while preserving existing defaults and dry-run behavior. Do not document credentials or private URLs.

**Step 3: Verify docs/config consistency**

Run the repository documentation/config checks if present.

**Step 4: Commit**

```bash
git add docs/runtime-automation.md scripts/automated-job-search.example.py
git commit -m "docs: describe detailed WhatsApp cards"
```

---

### Task 6: Full verification and operational smoke test

**Files:**
- Read: all changed files

**Step 1: Run full test suite**

```bash
python -m pytest -q
python -m pytest -q -m e2e
git diff --check
```

Expected: all tests pass, all e2e tests pass, and diff check is clean.

**Step 2: Verify no sensitive fields are rendered**

Search the formatter and launcher changes for descriptions, CV/profile fields, prompts, credentials, raw notes, and provider output. Confirm only bounded summaries reach the sender.

**Step 3: Deploy only with explicit authorization**

Do not edit production files or restart services as part of implementation without explicit deployment authorization. If authorized, deploy the launcher and run one controlled smoke test, recording only exit status and sanitized Hermes result.

**Step 4: Commit any final test-only fixes**

Use a focused commit if verification requires a correction; rerun the complete suite afterward.
