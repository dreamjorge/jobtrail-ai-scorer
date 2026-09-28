# Job feedback and balanced matching

Status: complete
Design: `docs/plans/2026-09-27-job-feedback-matching-design.md`
Plan: `docs/plans/2026-09-27-job-feedback-matching-plan.md`

## Scope

- Fix the two follow-up review findings from PR #81.
- Add a public Markdown matching strategy without reading private CV content into n8n.
- Add precise and exploratory classifications with explicit evidence labels.
- Add bounded one-time n8n feedback actions and synthetic local validation.

## Constraints

- JobTrail remains authoritative for discovery, scoring, classification, and journal.
- n8n remains disabled-by-default, local-only, and has no scheduler.
- No automatic applications.
- No CV/profile/prompt/credential data in n8n payloads or fixtures.
- Preserve existing envelope and score compatibility.

## Tasks

1. [x] Harden the legacy health payload validation and sanitize supplied notification run IDs. Commit: `3b03184`.
2. [x] Add the versioned public matching strategy loader and Markdown. Commit: `0c077c5`.
3. [x] Add dual scores, evidence labels, and deterministic classification. Commit: `789d8e2`.
4. [x] Extend the sanitized n8n envelope with bounded feedback action descriptors. Commit: `3bc358c`.
5. [x] Add the local n8n feedback callback fixture and static validation. Commit: `b3f17e7`.
6. [x] Run full verification and prepare a separate PR. Verification: 792 tests passed, compileall, shellcheck, JSON validation, and diff check passed.

## Evidence

Implementation uses strict RED → GREEN → TRIANGULATE per task. Each completed task requires focused tests, review, and a work-unit commit. Delivery/PR remains separate from review approval.

Hardening evidence: dry-run identity normalization, stable equal-score ordering, and non-positive score caps are covered in commit `abd9aa8`; bounded dry-run output/clock/input handling is covered in commit `a5ec0e4`; focused tests and spec/quality reviews passed.
