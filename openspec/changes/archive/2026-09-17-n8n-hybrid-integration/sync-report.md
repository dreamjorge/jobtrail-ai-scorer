# SDD Sync Report: n8n-hybrid-integration

- **status:** synced
- **change:** `n8n-hybrid-integration`
- **artifact store:** `openspec/both`
- **skill resolution:** `paths-injected`

## Domains and canonical files

- **Domain synced:** `n8n-handoff`
- **Canonical file updated:** `openspec/specs/n8n-handoff/spec.md`
- The canonical domain file did not previously exist, so the verified domain delta was copied as the initial canonical specification.

## Requirement changes

- **ADDED:**
  - Canonical scheduler and ownership
  - Versioned bounded redacted envelope
  - Stable identity and acknowledgement
  - Journal and launcher ordering
  - Failure behavior
  - Dry-run exclusion
  - Hermes independence
  - Disable and rollback
  - Private-data and secret boundaries
  - Deferred capabilities
- **MODIFIED:** none
- **REMOVED:** none
- **RENAMED:** none

## Collision and guardrails

- Active same-domain collision: **none detected**. The active-change scan found only `n8n-hybrid-integration` for `n8n-handoff`.
- A legacy flat `openspec/changes/n8n-hybrid-integration/spec.md` exists, but a domain delta also exists, so the legacy-only blocking condition does not apply.
- Destructive sync approval: not required; this sync contains only ADDED requirements.
- `openspec/config.yaml` was absent; no `rules.sync` override was available.

## Validation

- Read proposal, domain delta, design, tasks, and verify report.
- Verify report is clearly passing: 10/10 requirements, 10/10 scenarios, 0 blockers, 0 critical findings, and test/build exit code 0.
- Confirmed the canonical spec and verify report exist.
- Ran `git diff --check -- openspec/specs/n8n-handoff/spec.md`; passed.
- No source, test, runtime, service, network, Docker, systemd, Hermes, WhatsApp, n8n, archive, or commit operation was performed.

## Structured status and action context

- Change selection: `n8n-hybrid-integration` (unambiguous).
- Parent status: apply all tasks complete (`10/10`), verify report present and PASS.
- Mode: `repo-local`.
- Authoritative workspace: `/root/.config/superpowers/worktrees/jobtrail-ai-scorer/feat-automation-dry-run-issue-36`.
- Allowed edit root: the workspace; requested write surfaces were respected.
- Delivery context: single PR with explicit `size:exception`; sync made no source changes.

## Next recommended phase

`sdd-archive` when the parent is ready. This sync leaves the change active and does not move or archive it.
