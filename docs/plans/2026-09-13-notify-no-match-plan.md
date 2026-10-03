# No-Match WhatsApp Notification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Send a bounded WhatsApp summary for clean real automation runs that find no qualifying job, while preserving dry-run safety and existing selected/failure notifications.

**Architecture:** Extend the existing notification composition boundary rather than adding a second notifier path. Represent the no-match result in `AutomationRun`/journal classification so runtime observability and notification behavior share one outcome model.

**Tech Stack:** Python, pytest, dataclasses, JSONL run journal, existing shell WhatsApp helper.

---

### Task 1: Add failing message-composition tests

**Files:**
- Modify: `tests/test_automation.py` or the existing notification-focused test module found by `grep -R "_compose_notification\|notification_kind" tests`
- Test: same file

**Step 1: Write the failing tests**

Add tests that assert:

- a clean no-match run with notifications enabled returns the exact bounded summary containing searched count, scored count, and score threshold;
- a selected-job run still returns the existing job card;
- a failure run still follows the existing failure notification rule;
- disabled notifications return no message.

**Step 2: Run focused tests**

Run: `python -m pytest -q <focused-test-file> -k 'no_match or notification'`

Expected: the new no-match test fails because `_compose_notification` currently returns `None`.

**Step 3: Commit the red tests**

```bash
git add tests/<focused-test-file>
git commit -m "test(automation): specify no-match notification"
```

### Task 2: Add failing pipeline and journal tests

**Files:**
- Modify: `tests/test_automation.py` or the existing automation pipeline test module
- Modify: `tests/test_run_journal.py` or the existing journal test module

**Step 1: Write failing tests**

Add tests that run a clean no-match real pipeline with a recording notifier and assert the notifier receives one message. Add a journal test asserting a run with `notification_sent=True`, no selected job, and no failures is classified as `notification_kind == "no_match"`.

Also add/retain a dry-run test proving the notifier is not called.

**Step 2: Run focused tests**

Run: `python -m pytest -q <automation-tests> <journal-tests> -k 'no_match or notification_kind or dry_run'`

Expected: the new assertions fail before implementation.

**Step 3: Commit the red integration tests**

```bash
git add tests/<automation-tests> tests/<journal-tests>
git commit -m "test(journal): cover no-match outcome"
```

### Task 3: Implement no-match notification

**Files:**
- Modify: `src/jobtrail_ai_scorer/automation.py`
- Modify: `src/jobtrail_ai_scorer/run_journal.py`

**Step 1: Implement the minimal composition change**

Extend `_compose_notification()` so that, after selected and failure branches are considered, `notify_enabled and not selected and not failures` returns a bounded no-match summary with the run counts and threshold. Pass `score_threshold` into the composition boundary using the existing config flow; do not read profile/CV/job fields.

**Step 2: Implement journal classification**

Update `_classify_notification_kind()` to return `no_match` when there is no selected job and no failures but the run has `notification_sent=True`, or use the existing outcome inputs in a way that preserves `none` for dry-run/planned-only records. Keep `notified` sourced from `notification_sent` when available.

**Step 3: Run focused tests**

Run: `python -m pytest -q <automation-tests> <journal-tests> -k 'no_match or notification_kind or dry_run'`

Expected: PASS.

**Step 4: Commit the implementation**

```bash
git add src/jobtrail_ai_scorer/automation.py src/jobtrail_ai_scorer/run_journal.py
git commit -m "feat(automation): notify when no jobs qualify"
```

### Task 4: Triangulate and refactor

**Files:**
- Modify: tests and implementation only if evidence requires it

**Step 1: Run the complete test suite**

Run: `python -m pytest -q`

Expected: all tests pass.

**Step 2: Run Ruff**

Run: `ruff check src/jobtrail_ai_scorer/automation.py src/jobtrail_ai_scorer/run_journal.py tests`

Expected: no violations.

**Step 3: Inspect the final diff**

Run: `git diff origin/main...HEAD --stat && git diff origin/main...HEAD --check`

Expected: only no-match design, notification implementation, journal classification, and tests are changed; no credentials or private profile/CV content appears.

**Step 4: Commit any narrowly scoped refactor**

```bash
git add <verified-files>
git commit -m "refactor(automation): simplify no-match notification flow"
```

### Task 5: Verify runtime contract

**Files:**
- No source changes unless verification finds a defect.

**Step 1: Run the focused notification tests again**

Run: `python -m pytest -q <focused-tests>`

Expected: PASS.

**Step 2: Confirm dry-run safety**

Use existing tests only; do not run the live notifier or Hermes during local verification.

**Step 3: Report readiness**

Report test results and the branch/commit state. Do not push or open a PR without explicit approval.
