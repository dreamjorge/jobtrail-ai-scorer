# Job feedback and balanced matching Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve match precision and coverage with explicit strategy/evidence scoring, then collect bounded user feedback through local n8n action links without exposing CV or private profile data.

**Architecture:** Keep JobTrail authoritative for discovery, scoring, classification, journal, and run identity. Add a versioned public matching-strategy Markdown file and additive sanitized envelope fields. n8n renders one-time local action links and submits bounded feedback events; it does not read the CV, rescore jobs, or schedule searches.

**Tech Stack:** Python, Pydantic/dataclasses already used by JobTrail, pytest, n8n validation Compose fixture, JSON webhook payloads, Markdown strategy configuration.

---

### Task 1: Address the two follow-up review findings

**Files:**
- Modify: `src/jobtrail_ai_scorer/discover.py`
- Modify: `src/jobtrail_ai_scorer/automation.py`
- Test: `tests/test_discover.py`
- Test: `tests/test_automation.py`

**Step 1: Write failing tests**

- Add a discovery test where `/api/health` and `HEAD /api/health` return 404, `/health` returns 2xx with a non-JobTrail body, and discovery rejects it.
- Add a notification test with a supplied run ID containing a forbidden token and an over-limit value; assert the emitted summary is sanitized and bounded.

**Step 2: Run focused tests**

Run: `pytest -q tests/test_discover.py tests/test_automation.py`
Expected: FAIL on both new regression tests.

**Step 3: Implement minimal fixes**

- Validate the legacy `/health` response using a bounded JobTrail-specific payload/status contract rather than accepting arbitrary 2xx.
- Route supplied run IDs through the same clipping/scrubbing path as other notification fields before storing them in the summary.

**Step 4: Run focused tests**

Run: `pytest -q tests/test_discover.py tests/test_automation.py`
Expected: PASS.

**Step 5: Commit after explicit delivery authorization**

`git add src/jobtrail_ai_scorer/discover.py src/jobtrail_ai_scorer/automation.py tests/test_discover.py tests/test_automation.py && git commit -m "fix: harden review follow-ups"`

---

### Task 2: Add the public matching strategy document and loader

**Files:**
- Create: `config/matching-strategy.md`
- Create: `src/jobtrail_ai_scorer/matching_strategy.py`
- Modify: `src/jobtrail_ai_scorer/config.py`
- Modify: `src/jobtrail_ai_scorer/main.py`
- Test: `tests/test_matching_strategy.py`

**Step 1: Write failing tests**

Cover loading an existing strategy, missing/unreadable strategy behavior, bounded size, required section names, and rejection of forbidden private-path/CV content markers.

**Step 2: Run tests**

Run: `pytest -q tests/test_matching_strategy.py`
Expected: FAIL because the loader/config do not exist.

**Step 3: Implement minimal loader**

Add an optional local path setting with a safe default, bounded Markdown loading, stable section parsing, and a sanitized strategy object. The loader must never read or transmit the candidate CV through n8n.

**Step 4: Run focused tests**

Run: `pytest -q tests/test_matching_strategy.py`
Expected: PASS.

**Step 5: Commit after explicit delivery authorization**

`git add config/matching-strategy.md src/jobtrail_ai_scorer/matching_strategy.py src/jobtrail_ai_scorer/config.py src/jobtrail_ai_scorer/main.py tests/test_matching_strategy.py && git commit -m "feat: add versioned matching strategy"`

---

### Task 3: Add dual scores, evidence labels, and classification

**Files:**
- Modify: `src/jobtrail_ai_scorer/scoring.py`
- Modify: `src/jobtrail_ai_scorer/models.py`
- Test: `tests/test_scoring.py`
- Test: `tests/test_scoring_pure.py`

**Step 1: Write failing tests**

Test deterministic classification for `APPLY`, `REVIEW`, `EXPLORE`, and `SKIP`; separate `fit_score` and `coverage_score`; evidence labels `direct`, `equivalent`, `inferred`, `missing`; and backward-compatible parsing of existing score notes.

**Step 2: Run focused tests**

Run: `pytest -q tests/test_scoring.py tests/test_scoring_pure.py`
Expected: FAIL on new fields and classification behavior.

**Step 3: Implement minimal scoring extension**

Extend the schema additively, preserve the existing `score` field, inject only the loaded public strategy into the provider prompt, and require explicit evidence categories. Do not infer direct experience from adjacent skills.

**Step 4: Run focused tests**

Run: `pytest -q tests/test_scoring.py tests/test_scoring_pure.py`
Expected: PASS.

**Step 5: Commit after explicit delivery authorization**

`git add src/jobtrail_ai_scorer/scoring.py src/jobtrail_ai_scorer/models.py tests/test_scoring.py tests/test_scoring_pure.py && git commit -m "feat: classify precise and exploratory matches"`

---

### Task 4: Extend the sanitized n8n envelope with action descriptors

**Files:**
- Modify: `src/jobtrail_ai_scorer/n8n_outbound.py`
- Modify: `src/jobtrail_ai_scorer/automation.py`
- Test: `tests/test_n8n_outbound.py`
- Test: `tests/test_automation_e2e.py`

**Step 1: Write failing tests**

Assert optional classification/scores are bounded, action descriptors contain no private fields, identities are tied to `event_id`/`run_id`/job identity, expiry is bounded, and old envelopes remain valid.

**Step 2: Run focused tests**

Run: `pytest -q tests/test_n8n_outbound.py tests/test_automation_e2e.py`
Expected: FAIL on action fields and new score fields.

**Step 3: Implement additive envelope fields**

Add deterministic, opaque action IDs/descriptors. Do not place reusable secrets, CV text, profile text, prompts, credentials, or full job descriptions in the envelope. Keep n8n disabled by default and preserve existing retries/idempotency.

**Step 4: Run focused tests**

Run: `pytest -q tests/test_n8n_outbound.py tests/test_automation_e2e.py`
Expected: PASS.

**Step 5: Commit after explicit delivery authorization**

`git add src/jobtrail_ai_scorer/n8n_outbound.py src/jobtrail_ai_scorer/automation.py tests/test_n8n_outbound.py tests/test_automation_e2e.py && git commit -m "feat: add bounded feedback actions to handoffs"`

---

### Task 5: Add the local n8n feedback callback fixture

**Files:**
- Modify: `validation/n8n/docker-compose.yml`
- Create: `validation/n8n/workflows/job-feedback.json`
- Modify: `validation/n8n/README.md`
- Test: `tests/test_n8n_compose_validation.py`
- Create: `tests/fixtures/n8n/job-feedback-event.json`

**Step 1: Write failing static validation tests**

Verify the workflow is loopback-only, has no scheduler trigger, accepts only the documented feedback actions, rejects missing identity/expired/replayed tokens, and emits only the bounded feedback event.

**Step 2: Run tests**

Run: `pytest -q tests/test_n8n_compose_validation.py`
Expected: FAIL because the workflow and fixture do not exist.

**Step 3: Implement the validation fixture**

Add an operator-imported local workflow definition and documentation. The fixture must use synthetic data only, require explicit opt-in, and never access production Compose, credentials, CV/profile files, or public endpoints.

**Step 4: Run focused tests**

Run: `pytest -q tests/test_n8n_compose_validation.py`
Expected: PASS.

**Step 5: Commit after explicit delivery authorization**

`git add validation/n8n/docker-compose.yml validation/n8n/workflows/job-feedback.json validation/n8n/README.md tests/test_n8n_compose_validation.py tests/fixtures/n8n/job-feedback-event.json && git commit -m "feat: add local n8n feedback workflow"`

---

### Task 6: Run full verification and prepare review

**Files:**
- Test: all repository tests and static checks
- Modify: `docs/runtime-automation.md` if public operator behavior changed

**Step 1: Run verification**

Run:

```sh
pytest -q
python -m compileall -q src
shellcheck scripts/*.sh
 git diff --check
```

Expected: all tests and checks pass.

**Step 2: Review data boundary**

Confirm no CV/profile/private path/credential content enters n8n payloads, fixtures, logs, or notifications.

**Step 3: Commit documentation with the relevant work unit**

Use a Conventional Commit only after explicit authorization.

**Step 4: Create a separate issue/PR**

Keep the two PR #81 review fixes separate from the larger matching/feedback feature if their scope remains independently releasable. Link the new issue and include focused test evidence.
