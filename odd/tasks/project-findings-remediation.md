# Project findings remediation

Status: in progress; workflow migrated from SDD follow-up to ODD execution.

## Scope
Revalidate current findings across dry-run, scorer, n8n validation, and WhatsApp/Hermes runtime without reopening resolved work without current evidence. Existing OpenSpec artifacts remain preserved as historical evidence; new remediation and verification proceed through ODD tasks.

## Constraints
- Preserve JobTrail as scheduler/discovery/scoring authority.
- Keep n8n disabled by default and local-only.
- Do not inspect CV, private profiles, credentials, or `.env` values.
- No push/PR; commits require explicit authorization.
- Treat historical OpenSpec evidence as historical until current checks reproduce it.

## Route
Delegated direct verification first; delegate any multi-file source change only after a current failing test identifies the defect.

## Tasks
1. Run current dry-run and scorer tests; classify findings.
2. Fix only confirmed implementation defects using TDD.
3. Reverify and document current evidence.
4. Record current n8n evidence in ODD and reconcile historical OpenSpec artifacts without fabricating missing reports.

## Evidence
Initial map: delegated read-only findings inventory returned 2026-09-27.
WhatsApp formatter and Hermes runtime pairing are complete; synthetic message delivery succeeded.
Current verification: dry-run 24 passed; scorer 35 passed; no actionable defect.
n8n validation: local fixture started with temporary `n8nio/n8n:latest`, health returned `{"status":"ok"}`, and both `/webhook-test/jobtrail-handoff` and published `/webhook/jobtrail-handoff` returned HTTP 200 with `{"accepted":true}` for synthetic envelopes. A controlled JobTrail run with temporary `N8N_ENABLED=1` and the published loopback endpoint completed with `searched=7`, `imported=0`, `scored=0`, no failures; WhatsApp no-match notification was delivered; the run journal recorded n8n delivery `status=accepted`, `attempts=1`, and event id `3fa28bbad9ec8fdeedfbca022f121291`. Historical OpenSpec disposition: `n8n-hybrid-integration` is treated as archived based on its existing `archive-report.md`, `sync-report.md`, and archived path; its earlier blocked `archive.md` is retained as a contradictory historical snapshot. `n8n-local-compose-validation` is apply-complete but remains an active historical record pending native verification/archive evidence. The current ODD smoke evidence is recorded here separately and does not rewrite those historical reports. OpenSpec reconciliation is no longer the active execution workflow.

Search automation environment loading: quoted the shell-sensitive values on lines 3-4 of `/DATA/AppData/jobtrail/search-automation.env` without exposing their contents. `bash -n` and a clean shell source check now pass without command-not-found warnings.
