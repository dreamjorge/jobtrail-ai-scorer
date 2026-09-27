# Bot findings runtime safety

Status: approved; implementation in progress.

## Scope
Resolve the three actionable automated-review findings from merged PR #79: discovery compatibility, journal concurrency, and canonical run ID reuse.

## Constraints
- Preserve 5xx/timeouts/transport failures as unavailable.
- Preserve atomic journal replacement, permissions, corrupted-line tolerance, and injectable writers.
- Preserve bounded/redacted notifications and existing run ID format.
- Keep JobTrail authoritative and n8n one-way, local-only, disabled-by-default.
- No credentials/CV/private profiles.

## Route
Delegated direct implementation for multi-file behavior and tests, strict TDD per finding.

## Issue / branch
- Approved issue: #80
- Branch: `fix/bot-findings-runtime-safety`

## Tasks
1. Add discovery fallback test and implementation.
2. Add concurrent journal test and shared lock wiring.
3. Add canonical run ID test and propagation.
4. Run full verification, create PR, and merge after checks.
