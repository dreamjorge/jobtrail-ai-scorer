# Automation Dry-Run — Implementation Plan

Issue: #36

## Phase 1 — Structured scorer output

1. Add `--json` to `score` and a `run_score(..., output_json=False)` path.
2. In scorer dry-run, serialize only validated score payloads; preserve current human output when `--json` is absent.
3. Add tests for JSON schema, dry-run no-note behavior, malformed provider output, and sensitive-field exclusion.

## Phase 2 — Automation simulation

1. Add `dry_run: bool = False` to `AutomationConfig` and parse `JOBTRAIL_AUTOMATION_DRY_RUN`.
2. Add launcher `--dry-run` with CLI precedence.
3. Add `dry_run`, planned-operation counts, and bounded `notification_preview` to `AutomationRun`.
4. Gate import, SeenCache writes, score-note readback, notifier, breaker persistence, and journal persistence.
5. Add injected scorer-preview seam for deterministic tests while production uses structured scorer JSON.

## Phase 3 — Hermetic verification and docs

1. Add unit tests asserting no import, note, cache, breaker, journal, or WhatsApp writes.
2. Add deterministic e2e dry-run fixture and compare repeated JSON output.
3. Document distinction between scorer-only `score --dry-run` and full automation `--dry-run`.
4. Verify full suite and e2e suite.

## Slice strategy

- PR-F1: structured scorer JSON output + tests.
- PR-F2: automation dry-run gates + tests.
- PR-F3: launcher/e2e/docs + final verification.
