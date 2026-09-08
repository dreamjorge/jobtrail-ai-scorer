# Adzuna Source Adapter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an optional Adzuna API source adapter that preserves the source-adapter contract from #38 and the partial-failure semantics from #32.

**Architecture:** Module `src/jobtrail_ai_scorer/sources/adzuna.py` implements `SourceAdapter`. Reuse existing `RetryPolicy`, `NormalizedJob`, and `SourceSearchRequest`. Inject `httpx.Client` for tests via `httpx.MockTransport`.

**Tech Stack:** Python 3.11, httpx, dataclasses, pytest, MockTransport.

---

## Guardrails

- Strict TDD: failing test first, run RED, then minimal code, then GREEN.
- Secrets are never logged, never included in failures, never added to imports or notifications.
- If the diff exceeds 400 changed lines, slice into:
  1. Adapter + parsing/normalization.
  2. HTTP/retry/integration/tests.
- Do not commit unless explicitly asked.

## Task 1: Adzuna normalization

**Files:**
- Create: `src/jobtrail_ai_scorer/sources/adzuna.py`
- Modify: `src/jobtrail_ai_scorer/sources/__init__.py`
- Modify or create: `tests/test_adzuna.py`

**Steps:**
1. Failing tests for `AdzunaSourceAdapter.search()`:
   - happy path with nested company/location and full fields.
   - missing optional fields omitted.
2. Implement `AdzunaSourceAdapter.search()` accepting an injected raw gateway for tests.
3. Re-export `AdzunaSourceAdapter` from `sources/__init__.py`.
4. Run focused normalization tests.

## Task 2: Configuration + disable semantics

**Files:**
- Modify: `src/jobtrail_ai_scorer/sources/adzuna.py`
- Modify: `tests/test_adzuna.py`

**Steps:**
1. Failing tests for `AdzunaConfig.from_env`:
   - missing both credentials disables silently.
   - missing one credential raises `AdzunaConfigError` or warns and disables; pick the safer default and document it.
   - `JOB_DISABLE_ADZUNA=1` disables regardless.
2. Implement `AdzunaConfig.from_env` and adapter construction.
3. Run focused tests.

## Task 3: HTTP gateway, retry, partial failure

**Files:**
- Modify: `src/jobtrail_ai_scorer/sources/adzuna.py`
- Modify: `tests/test_adzuna.py`
- Modify: `tests/test_automation.py`

**Steps:**
1. Failing tests using `httpx.MockTransport`:
   - 200 OK returns normalized jobs.
   - 4xx terminal.
   - 5xx retryable recovers on retry.
   - exhausted retries bubble up.
2. Reuse existing `RetryPolicy` for the GET.
3. Automation integration test: Adzuna raised adapter exception is recorded as `search:...` failure, JobSpy results still imported.
4. Run focused and full tests.

## Task 4: Docs and final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/runtime-automation.md`
- Create: docs/plans/2026-09-08-adzuna-source-{design,plan}.md (already created)

**Steps:**
1. Document env vars and disable behavior.
2. Note the boundary (no pagination, secrets never logged).
3. Run full test suite: `python -m pytest -q`.
4. Inspect `git diff --stat`; if over 400 lines, slice before opening PR.
