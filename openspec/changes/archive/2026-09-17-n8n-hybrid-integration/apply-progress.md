# Apply Progress: n8n Hybrid Integration

## Status
- Native status consumed: `gentle-ai sdd-status@2`, `applyState: ready`, `nextRecommended: apply`.
- Action context: `repo-local`; all edits stayed within the authorized workspace and listed edit surfaces.
- Strict TDD active; runner: `python -m pytest`.
- Tasks complete: 10/10. No remaining implementation tasks.

## Completed Tasks and Persisted Checkboxes
All implementation-owned rows 1.1 through 4.2 are marked `- [x]` in `tasks.md`.

## TDD Cycle Evidence
| Task group | RED evidence | GREEN evidence | TRIANGULATE | REFACTOR |
|---|---|---|---|---|
| 1.1 / 2.1 outbound adapter | `python -m pytest tests/test_n8n_outbound.py` failed during collection with missing `n8n_outbound` module | Focused outbound suite passed: 5 tests | Full suite passed: 672 tests; stable event ID, redaction, retries, disabled/dry-run covered | Bounded adapter kept injected `httpx.MockTransport`; no generic webhook abstraction |
| 1.2 / 2.2 automation context | New run-context/config assertions failed before fields existed | `python -m pytest tests/test_automation.py tests/test_automation_e2e.py` passed: 59 tests | Full suite preserved SeenCache, scoring, and Hermes notifier behavior | Added optional config field and additive `run_id` without changing pipeline ownership |
| 1.3 / 2.3 journal and launcher | Journal collection failed because `_delivery_line` was absent | `python -m pytest tests/test_run_journal.py tests/test_automated_job_search_launcher.py` passed: 10 tests | Full suite passed; local record is written before delivery and dry-run exits before handoff | Delivery records are additive JSONL records with allowlisted fields |
| 3.1 / 3.2 / 4.x | Event-ID triangulation assertion first failed against pre-spec identity | Full suite passed: 672 tests | Sensitive/oversized payload, retry, disabled-by-default, and Hermes isolation evidence passed | Runtime documentation records rollback and deferred boundaries |

## Work Unit Evidence
| Work unit | Focused command and result | Runtime harness | Rollback boundary |
|---|---|---|---|
| 1 envelope/config/adapter | `python -m pytest tests/test_n8n_outbound.py` — 5 passed | N/A; injected MockTransport only | Revert `n8n_outbound.py` and config wiring |
| 2 context/journal/launcher | `python -m pytest tests/test_automation.py tests/test_automation_e2e.py tests/test_run_journal.py tests/test_automated_job_search_launcher.py` — 121 passed with outbound coverage | N/A; monkeypatched launcher seams and in-process e2e fakes | Revert automation, journal, and launcher changes |
| 3 integration/docs | `python -m pytest` — 672 passed | N/A; no live runtime services allowed | Revert focused tests and runtime documentation |

## Files Changed
- `src/jobtrail_ai_scorer/n8n_outbound.py` — new bounded versioned envelope and injected HTTP adapter.
- `src/jobtrail_ai_scorer/automation.py` — optional n8n config and stable run context fields.
- `src/jobtrail_ai_scorer/run_journal.py` — additive linked delivery records.
- `scripts/automated-job-search.example.py` — local journal before optional delivery; dry-run remains local-only.
- `tests/test_n8n_outbound.py`, `tests/test_automation.py`, `tests/test_run_journal.py` — hermetic contract evidence.
- `docs/runtime-automation.md` — rollout, rollback, ownership, secrets, and deferred scope.
- `openspec/changes/n8n-hybrid-integration/tasks.md` — all ten implementation checkboxes persisted.

## Commits
- `4e4a668 test: define n8n outbound contract`
- `0c638b2 test: specify run context handoff`
- `f7f87a1 test: specify delivery ordering`
- `f372e66 feat: add n8n outbound adapter`
- `5a962a8 feat: expose automation handoff context`
- `8c700b0 feat: journal n8n delivery ordering`
- `ef94b29 test: verify n8n redaction boundaries`
- `fc9d990 test: triangulate n8n integration`
- `dfadd28 refactor: simplify bounded n8n integration`
- `6efbf11 docs: document n8n rollout boundaries`

## Deviations
The design's one-way boundary is preserved. The launcher records a disabled delivery classification locally even when n8n is disabled, while dry-run bypasses both local durable recording and delivery acknowledgement as required. No live network, Docker, systemd, Hermes, WhatsApp, or public service was invoked.

## Review Workload / PR Boundary
- Strategy: single PR under `ask-on-risk`; forecast remained Low risk and below 400 changed lines for authored implementation scope.
- Boundary: complete approved n8n-hybrid-integration slice, no deferred callbacks, ingress, write-back, collection, or runtime infrastructure.

## Verification
Final command: `python -m pytest` — 672 passed in 25.27 seconds.
