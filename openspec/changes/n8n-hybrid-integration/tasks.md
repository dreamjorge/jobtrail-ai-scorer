# Tasks: Bounded n8n Hybrid Integration

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 280–390 |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR; split before apply if scope exceeds 400 lines |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Envelope/config/adapter | Single | `python -m pytest tests/test_n8n_outbound.py` | N/A; injected MockTransport is hermetic | Revert `n8n_outbound.py` and config wiring |
| 2 | Context/journal/launcher | Single | `python -m pytest tests/test_run_journal.py tests/test_automated_job_search_launcher.py` | N/A; monkeypatched launcher seams | Revert launcher/journal integration |
| 3 | Integration/docs | Single | `python -m pytest` | N/A; no runtime services allowed | Revert focused tests and `docs/runtime-automation.md` |

## Phase 1: RED — Contracts and Safety Tests

- [x] 1.1 Add failing `tests/test_n8n_outbound.py` cases for exact versioned envelope, stable SHA-256 event IDs, clipping/allowlist redaction, 2xx acknowledgement, 4xx/no-retry, 5xx/timeout bounded retries, disabled/missing endpoint, and dry-run no-send; command `python -m pytest tests/test_n8n_outbound.py`; commit `test: define n8n outbound contract`. <!-- sdd-owner: implementation -->
- [x] 1.2 Add failing tests in `tests/test_automation.py` and `tests/test_automation_e2e.py` for one run context, selected-summary handoff, unchanged SeenCache/import/scoring, and Hermes isolation; command `python -m pytest tests/test_automation.py tests/test_automation_e2e.py`; commit `test: specify run context handoff`. <!-- sdd-owner: implementation -->
- [x] 1.3 Add failing journal/launcher tests in `tests/test_run_journal.py` and `tests/test_automated_job_search_launcher.py` for local-record-before-delivery, linked delivery classifications, disabled behavior, local-vs-delivery failure, and dry-run absence of acknowledgement; command `python -m pytest tests/test_run_journal.py tests/test_automated_job_search_launcher.py`; commit `test: specify delivery ordering`. <!-- sdd-owner: implementation -->

## Phase 2: GREEN — Minimal Implementation

- [x] 2.1 Create `src/jobtrail_ai_scorer/n8n_outbound.py` with bounded envelope builder, deterministic identity, injected HTTP client/transport, finite retry policy, `DeliveryResult`, redaction, and no generic webhook behavior; make 1.1 pass with `python -m pytest tests/test_n8n_outbound.py`; commit `feat: add n8n outbound adapter`. <!-- sdd-owner: implementation -->
- [x] 2.2 Modify `src/jobtrail_ai_scorer/automation.py` to expose stable run context and one allowlisted selected summary without changing SeenCache or Hermes paths; make 1.2 pass with `python -m pytest tests/test_automation.py tests/test_automation_e2e.py`; commit `feat: expose automation handoff context`. <!-- sdd-owner: implementation -->
- [x] 2.3 Modify `src/jobtrail_ai_scorer/run_journal.py` for additive linked delivery records, atomic JSONL, schema compatibility, and secret exclusion; wire `scripts/automated-job-search.example.py` so local outcome precedes optional delivery and disabled/dry-run paths stay local; make 1.3 pass with `python -m pytest tests/test_run_journal.py tests/test_automated_job_search_launcher.py`; commit `feat: journal n8n delivery ordering`. <!-- sdd-owner: implementation -->

## Phase 3: TRIANGULATE — Full Hermetic Evidence

- [x] 3.1 Add sensitive/oversized fixtures and assert payload, logs, and journal omit descriptions, notes, prompts, CV/profile data, credentials, and provider blobs; run `python -m pytest`; commit `test: verify n8n redaction boundaries`. <!-- sdd-owner: implementation -->
- [x] 3.2 Verify retry/idempotency, disabled-by-default config, dry-run exclusion, launcher ordering, local failure retention, and Hermes non-invocation across focused tests and `python -m pytest`; commit `test: triangulate n8n integration`. <!-- sdd-owner: implementation -->

## Phase 4: REFACTOR — Rollout and Regression

- [x] 4.1 Refactor only after green evidence, preserving public contracts and existing tests; run `python -m pytest`; commit `refactor: simplify bounded n8n integration`. <!-- sdd-owner: implementation -->
- [x] 4.2 Update `docs/runtime-automation.md` with sole scheduler/SeenCache ownership, variables, auth-secret boundary, disable/rollback, journal observability, hermetic verification, and explicit deferred Telegram callbacks/public HTTPS/authenticated write-back/Google Sheets/n8n collection; run `python -m pytest`; commit `docs: document n8n rollout boundaries`. <!-- sdd-owner: implementation -->
