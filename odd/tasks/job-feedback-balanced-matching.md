# Job feedback and balanced matching

Status: PR #83 remediation verified; publication authorized
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

## PR #83 review remediation

Authorized: fix five verified findings; user subsequently authorized commit and push to PR #83. No merge or deployment.

Publication checks: 799 tests passed again before commit; shellcheck and diff check passed. Native review inspection stopped at `managed_assets_outdated`; no native approval is claimed and harness assets were not modified.

- [x] Wire explicit feedback opt-in into the canonical launcher (`N8N_FEEDBACK_ACTIONS_ENABLED`, default off).
- [x] Use coverage for adjacent EXPLORE classification, retaining hard exclusions.
- [x] Correct the n8n Code-node static-data helper and test executable behavior with Node stubs.
- [x] Preserve bounded structured evidence from the real ScoreResult schema.
- [x] Package the default strategy and verify wheel extraction/import outside the checkout with offline build flags.
- [x] Run independent review and complete regression verification: 799 passed, zero skips; compileall, shellcheck and diff check passed.

Remediation evidence: source/test/doc fixes committed as `911466f1828e66f9e1bd7daeddefadd70f71e90e`; push authorized by user. Writer timed out; independent review recovered and approved source changes. Initial verification was polluted by generated scratch under tests; artifacts were preserved outside the checkout and normal pytest then passed. RED provenance for the five source fixes is unavailable; do not claim strict TDD compliance. Wheel test portability fix has observed RED/GREEN. Node stubs do not prove real n8n persistence/concurrency or deployment readiness. Packaged/repository strategy copies currently match; future drift remains a maintenance risk.

## Evidence

Implementation uses strict RED → GREEN → TRIANGULATE per task. Each completed task requires focused tests, review, and a work-unit commit. Delivery/PR remains separate from review approval.

Hardening evidence: dry-run identity normalization, stable equal-score ordering, and non-positive score caps are covered in commit `abd9aa8`; bounded dry-run output/clock/input handling is covered in commit `a5ec0e4`; focused tests and spec/quality reviews passed.
