# WhatsApp Notification Formatting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace compact JSON match notifications with readable WhatsApp text while preserving the bounded notification contract.

**Architecture:** Keep `NotificationBuilder` as the allowlist/redaction boundary. Add a small renderer at the orchestration boundary that formats its resulting summary into deterministic text; no provider, Hermes, or runtime configuration changes.

**Tech Stack:** Python, pytest, existing `jobtrail_ai_scorer` automation tests.

---

### Task 1: Add the failing formatter tests

**Files:**
- Modify: `tests/test_automation.py`

**Steps:**
1. Add a focused test for selected-match output with title, score, recommendation, links, strengths, gaps, and run ID.
2. Assert the output is readable text rather than compact JSON.
3. Assert empty location is omitted.
4. Run the focused test and observe the expected failure because the current path emits JSON.

### Task 2: Implement the readable formatter

**Files:**
- Modify: `src/jobtrail_ai_scorer/automation.py`
- Modify: `tests/test_automation.py`

**Steps:**
1. Add a small private renderer for the allowlisted summary.
2. Render optional fields only when non-empty.
3. Use bullets for strengths and gaps and preserve existing bounded values.
4. Replace only the selected-match `json.dumps` call with the renderer.
5. Run focused tests and confirm they pass.

### Task 3: Verify the change

**Files:**
- No source changes expected.

**Steps:**
1. Run focused automation formatter tests.
2. Run the relevant automation and notification test modules.
3. Run the full available Python test suite if practical.
4. Report any pre-existing or environment-specific failures separately.
