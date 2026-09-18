# Archive Report: n8n-hybrid-integration

## Status
**BLOCKED — archive not completed.**

The candidate verification evidence was persisted verbatim to `verify-report.md` (`cmp` confirmed identical). The native status reported apply all_done, tasks 10/10, dependencies ready, and next `archive`. However, file-backed archive preconditions are not satisfied:

1. The change has only the legacy flat `spec.md`; no `specs/{domain}/spec.md` delta exists. The archive contract explicitly blocks a file-backed archive when a legacy flat spec is the only spec artifact.
2. No successful `sync-report.md` exists. Archive-time sync fallback was not explicitly approved.
3. Therefore no canonical spec sync or archive-folder move was performed.

No source, tests, runtime, or non-SDD files were edited. No network, Docker, systemd, Hermes, WhatsApp, n8n, or public service was invoked.

## Artifacts Read
- `proposal.md`
- `spec.md` (legacy flat spec)
- `design.md`
- `tasks.md`
- `apply-progress.md`
- `/tmp/n8n-verify-report.md` (read-only candidate evidence)
- native `gentle-ai sdd-status n8n-hybrid-integration`

## Verification Evidence
The persisted candidate report records:

- Verdict: PASS
- 676 full pytest tests passed
- 19 focused tests passed
- Scoped Ruff passed
- `compileall` passed
- Requirements: 10/10
- Scenarios: 10/10
- Blockers: 0; critical findings: 0
- No external services were used

Formal verify-report persistence through the retired `gentle-ai sdd-verify-validate` command was unavailable. The supplied candidate evidence is treated as read-only final-state evidence; no implementation blockers remain.

## Task Gate
`tasks.md` contains 10/10 completed implementation tasks. No unchecked `- [ ]` implementation task markers remain. No stale-checkbox reconciliation was needed.

## Specs Synced
None. Canonical sync was not attempted because the only spec artifact is the legacy flat `spec.md`, and no archive-time sync fallback approval was supplied.

## Requirement Names
No ADDED/MODIFIED/REMOVED operation sections were available to sync. The flat specification contains these ten requirements: Canonical scheduler and ownership; Versioned bounded redacted envelope; Stable identity and acknowledgement; Journal and launcher ordering; Failure behavior; Dry-run exclusion; Hermes independence; Disable and rollback; Private-data and secret boundaries; Deferred capabilities.

## Delivery Decision
The explicit user-approved single-PR `size:exception` is recorded: final diff is 818 lines including SDD artifacts. This does not override the missing canonical delta/sync preconditions.

## Status and Action Context
- Artifact store from native status: `openspec`
- Action context: `repo-local`
- Workspace root: `/root/.config/superpowers/worktrees/jobtrail-ai-scorer/feat-automation-dry-run-issue-36`
- Allowed edit root: the workspace root above
- Native next recommendation: `archive`

## Archive Path
No archive path was created. The active change remains at:
`openspec/changes/n8n-hybrid-integration/`

## Persistence Notes
`verify-report.md` was copied mechanically from `/tmp/n8n-verify-report.md` and verified byte-identical with `cmp`. No folder move occurred, so no archive move `diff -r` readback exists; this is a blocked phase, not a successful archive.

## Required Resolution
Create the canonical domain delta under `openspec/changes/n8n-hybrid-integration/specs/{domain}/spec.md`, run and persist a successful `sync-report.md` via `sdd-sync`, then rerun `sdd-archive`. Alternatively, provide explicit archive-time sync-fallback approval and resolve the legacy-flat-spec condition according to the archive contract.
