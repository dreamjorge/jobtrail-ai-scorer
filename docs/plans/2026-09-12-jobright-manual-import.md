# Safe Manual Jobright Import Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a confirmation-gated `import-jobright` CLI flow that accepts manually supplied Jobright data, imports it once with `jobright_manual` provenance, and reuses normal scoring/notification policy without any application automation.

**Architecture:** Add a small validated manual-input model and command boundary in `main.py`, backed by existing JobTrail HTTP import/list/get operations and the normalized source payload contract. Keep preview and confirmation above every mutation, use injectable prompts and dependencies for tests, and delegate post-import scoring to existing automation/scoring behavior rather than duplicating provider or notification logic.

**Tech Stack:** Python 3.11, Typer/Click CLI conventions already used by the project, Pydantic models, HTTPX, `SeenCache`, existing scorer and `NotificationBuilder`, pytest.

---

### Task 1: Map CLI and import seams

**Files:**
- Read: `src/jobtrail_ai_scorer/main.py`
- Read: `src/jobtrail_ai_scorer/automation.py`
- Read: `src/jobtrail_ai_scorer/jobtrail.py`
- Read: `src/jobtrail_ai_scorer/models.py`
- Read: `src/jobtrail_ai_scorer/seen_cache.py`
- Read: `tests/test_cli.py`
- Read: `tests/test_automation.py`

**Step 1: Confirm existing command conventions**

Identify how commands receive configuration, print JSON/results, prompt or confirm, return nonzero errors, and inject HTTP/scorer/notifier fakes. Confirm which existing client method can list jobs for duplicate detection.

**Step 2: Confirm the normalized import shape**

Identify the minimal mapping fields required by `NormalizedJob.to_import_payload()` and the backend import response identity. Do not add a Jobright source adapter or network fetch.

**Step 3: Run baseline tests**

```bash
python -m pytest tests/test_cli.py tests/test_automation.py -q
```

Expected: baseline passes.

---

### Task 2: Add validated manual input and normalization tests

**Files:**
- Create or modify: `src/jobtrail_ai_scorer/jobright_manual.py` (choose the smallest existing module seam confirmed in Task 1)
- Modify: `tests/test_cli.py` or create `tests/test_jobright_manual.py`

**Step 1: Write failing tests**

Cover:

- HTTPS URL normalization and rejection of malformed/non-HTTPS URLs;
- stable `sourceJobId` derived from the normalized URL;
- required title/company/location handling;
- optional private description retained for scorer input but absent from preview/public summary;
- normalized import payload with fixed `source=jobright_manual`;
- bounded values and no accidental inclusion of unexpected fields.

**Step 2: Run focused tests**

```bash
python -m pytest tests/test_jobright_manual.py tests/test_cli.py -q
```

Expected: collection or assertion failures because the input model/normalizer does not exist.

**Step 3: Implement the minimal model/normalizer**

Use strict validation for URL and required strings, deterministic URL canonicalization, bounded in-memory description handling, and an explicit method that returns only the backend import payload.

**Step 4: Run focused tests**

Expected: all input/normalization tests pass.

**Step 5: Commit**

```bash
git add src/jobtrail_ai_scorer/jobright_manual.py tests/test_jobright_manual.py tests/test_cli.py
git commit -m "feat(cli): validate manual Jobright input"
```

---

### Task 3: Add preview, confirmation, duplicate, and dry-run tests

**Files:**
- Modify: `src/jobtrail_ai_scorer/main.py`
- Modify: `src/jobtrail_ai_scorer/automation.py` or the manual flow module from Task 2
- Modify: `tests/test_cli.py`
- Modify: `tests/test_automation.py`
- Modify: `tests/stubs/stub_jobtrail.py`

**Step 1: Write failing command tests**

Add tests for:

- URL-only interactive follow-up prompts;
- complete non-interactive input;
- preview output containing only safe identity fields and fixed provenance;
- no `--confirm` causing zero import POSTs, cache writes, score calls, note writes, and notifier calls;
- `--dry-run` overriding `--confirm`;
- duplicate identity causing zero second POST;
- malformed/missing input failing before any mutation;
- backend import failure without retry or cache mutation.

**Step 2: Run focused tests**

```bash
python -m pytest tests/test_cli.py tests/test_automation.py -q
```

Expected: new tests fail because the command and confirmation gate do not exist.

**Step 3: Implement the command boundary**

Add `import-jobright` with explicit input options, injectable follow-up prompts, safe preview, `--confirm`, and `--dry-run`. Perform duplicate lookup before POST. Ensure no mutating call occurs before confirmation and no non-idempotent import retry is introduced.

**Step 4: Run focused tests**

Expected: all confirmation, duplicate, dry-run, and error tests pass.

**Step 5: Commit**

```bash
git add src/jobtrail_ai_scorer/main.py src/jobtrail_ai_scorer/automation.py tests/test_cli.py tests/test_automation.py tests/stubs/stub_jobtrail.py
git commit -m "feat(cli): gate manual Jobright imports"
```

---

### Task 4: Reuse normal scoring and notification policy after confirmation

**Files:**
- Modify: `src/jobtrail_ai_scorer/main.py`
- Modify: `src/jobtrail_ai_scorer/automation.py` only if a small reusable single-job seam is needed
- Modify: `tests/test_cli.py`
- Modify: `tests/test_automation_e2e.py`

**Step 1: Write failing post-import tests**

Cover confirmed import followed by:

- one normal scorer invocation with the existing CV/config;
- threshold filtering and allowlisted notification behavior;
- scoring failure retaining the imported job but suppressing notification;
- cache mark only after successful import;
- no lifecycle/application endpoint calls.

**Step 2: Implement post-import delegation**

Use existing scorer invocation and notification construction. Keep description private to scorer input and never copy it into preview, logs, or public summaries. Preserve one notification call and existing failure semantics.

**Step 3: Run focused E2E tests**

```bash
python -m pytest tests/test_cli.py tests/test_automation.py tests/test_automation_e2e.py -q
```

Expected: all manual import and existing automation tests pass.

**Step 4: Commit**

```bash
git add src/jobtrail_ai_scorer/main.py src/jobtrail_ai_scorer/automation.py tests/test_cli.py tests/test_automation_e2e.py
git commit -m "feat(cli): score confirmed Jobright imports safely"
```

---

### Task 5: Document the manual workflow and safety boundaries

**Files:**
- Modify: `README.md`
- Modify: `docs/runtime-automation.md`
- Modify: `tests/test_packaging.py` if command/help packaging assertions need updating

**Step 1: Document usage**

Show URL-only interactive usage and complete non-interactive usage without real URLs, credentials, descriptions, or recipient values. Explain preview, `--confirm`, `--dry-run`, duplicate behavior, and `jobright_manual` provenance.

**Step 2: Document non-goals**

Explicitly state there is no Jobright scraping/API integration, browser control, autofill, recruiter messaging, application submission, or lifecycle `applied` transition.

**Step 3: Update CLI/help/package tests**

Assert the command is discoverable and examples contain no private data.

**Step 4: Commit**

```bash
git add README.md docs/runtime-automation.md tests/test_packaging.py
 git commit -m "docs: describe safe manual Jobright import"
```

---

### Task 6: Full verification and delivery preparation

**Files:**
- Read: all changed files

**Step 1: Run complete verification**

```bash
python -m pytest -q
python -m pytest -q -m e2e
git diff --check
```

Expected: all tests pass, E2E tests pass, and diff check is clean.

**Step 2: Run privacy/safety checks**

Search changed files for scraping/browser automation, autofill, recruiter messaging, application submission, lifecycle `applied`, raw description logging, credentials, prompts, or private URLs. Confirm none are introduced.

**Step 3: Review the final diff**

Confirm all mutation paths are below the explicit confirmation gate, import POST is not retried, cache changes happen only after success, and scoring/notification reuse existing policy.

**Step 4: Open a PR only after explicit user authorization**

Do not merge or deploy automatically. Report the branch, commits, tests, and any unresolved design questions.
