# ATS Connectors Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement issue #40 with optional Lever and Greenhouse ATS adapters behind the existing source-adapter contract.

**Architecture:** Extract `JobSpySourceAdapter` and `normalize_jobspy_job` from `sources/__init__.py` into `sources/jobspy.py`. Add `JOB_ATS_BOARDS` JSON parsing on `AutomationConfig`. Add a `build_ats_adapters` factory that constructs `LeverSourceAdapter` and `GreenhouseSourceAdapter` instances from the parsed config. Each adapter follows the established `SourceAdapter` protocol and reuses the existing `RetryPolicy`/`retry_call` for HTTP.

**Tech Stack:** Python 3.11, dataclasses, httpx, pytest, MockTransport, JSON env parsing.

---

## Guardrails

- Strict TDD per task; failing test first.
- No secrets; the public ATS endpoints do not require authentication.
- No new dependencies; use `httpx` and the stdlib `html.parser` for stripping HTML descriptions.
- Slice into three PRs (plumbing, Lever, Greenhouse). Each PR keeps the suite green and stays under 400 changed lines.

## PR-A: plumbing and parser (no ATS endpoints yet)

**Files:**
- Create: `src/jobtrail_ai_scorer/sources/jobspy.py`
- Modify: `src/jobtrail_ai_scorer/sources/__init__.py`
- Modify: `src/jobtrail_ai_scorer/automation.py`
- Modify: `tests/test_sources.py` if needed
- Create: `tests/test_ats_boards.py`

**Steps:**
1. Failing tests in `tests/test_ats_boards.py`:
   - `parse_ats_boards(raw)` happy path returns dataclass with boards and `results_wanted` default.
   - invalid JSON raises `ValueError`.
   - non-object JSON raises `ValueError`.
   - unknown keys raise `ValueError`.
   - blank or duplicate tokens raise `ValueError`.
   - non-int `results_wanted` raises `ValueError`.
   - oversized cap clamps/raises per design.
2. Failing automation test:
   - when `JOB_ATS_BOARDS` is unset, `JobTrailAutomation` default `source_adapters` is unchanged from current behaviour (`(JobSpySourceAdapter,)`).
3. Implement parser using closed allowlist.
4. Extract `JobSpySourceAdapter` and helpers to `sources/jobspy.py`; re-export from `__init__.py`.
5. Run focused and full tests: `python -m pytest tests/test_ats_boards.py tests/test_sources.py tests/test_automation.py -q` and `python -m pytest -q`.

## PR-B: Lever adapter

**Files:**
- Create: `src/jobtrail_ai_scorer/sources/ats_common.py`
- Create: `src/jobtrail_ai_scorer/sources/lever.py`
- Modify: `src/jobtrail_ai_scorer/automation.py`
- Modify: `tests/test_automation.py`
- Create: `tests/test_ats_connectors.py`
- Modify: `README.md`
- Modify: `docs/runtime-automation.md`

**Steps:**
1. Failing tests using `httpx.MockTransport`:
   - happy path normalization for `LeverSourceAdapter.search()`.
   - cap honoured.
   - profile propagation.
   - 4xx terminal, 5xx retryable succeeds, 5xx exhaustion.
2. Failing automation test: Lever failure does not block JobSpy import.
3. Implement `ats_common._strip_html` and `_bounded`.
4. Implement `LeverSourceAdapter.search()` with retry policy and bounded GET.
5. Document `lever_boards` in README and runtime docs.
6. Run focused and full tests.

## PR-C: Greenhouse adapter

**Files:**
- Create: `src/jobtrail_ai_scorer/sources/greenhouse.py`
- Modify: `tests/test_ats_connectors.py`
- Modify: `tests/test_automation.py`
- Modify: `README.md`
- Modify: `docs/runtime-automation.md`

**Steps:**
1. Failing tests:
   - happy path normalization for `GreenhouseSourceAdapter.search()`.
   - cap honoured.
   - profile propagation.
   - 4xx terminal, 5xx retryable succeeds, 5xx exhaustion.
2. Failing automation test: Greenhouse failure does not block other sources.
3. Implement `GreenhouseSourceAdapter.search()`.
4. Document `greenhouse_boards` in README and runtime docs.
5. Run focused and full tests.
