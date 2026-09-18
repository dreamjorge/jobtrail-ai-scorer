# Design: Bounded n8n Hybrid Integration

## Technical Approach

Implement Approach 1 as a single, optional outbound adapter around the existing launcher. JobTrail remains the only scheduler, source collector, normalizer, importer, scorer, SeenCache owner, and journal authority. Hermes remains a separate local notifier; no credentials or messages are routed through n8n. The adapter accepts an injected `httpx` transport/client and is never a generic webhook framework.

## Architecture Decisions

| Decision | Alternatives | Rationale |
|---|---|---|
| One run-level HTTP handoff | n8n-owned collection; per-source forwarding | Preserves JobTrail identity, deduplication, redaction, and bounded retry semantics. |
| Configuration-gated adapter | Always-on delivery; implicit endpoint | Empty/false configuration is a safe no-network default and permits immediate rollback. |
| Stable run/event identities | Server-generated or retry-generated IDs | Retries must represent the same accepted event and must not imply a new job decision. |
| Hermes stays untouched | Move WhatsApp credentials into n8n | Keeps the local private channel operational and avoids expanding the secret boundary. |

## Data Flow

`launcher → JobTrailAutomation → SeenCache/import/score → local journal (pending) → n8n adapter → delivery journal`

The launcher creates one run context before work begins. After local work completes it writes the local outcome, then attempts the optional handoff. A second append records `accepted`, `failed`, `uncertain`, or `disabled`, linked by `run_id` and `event_id`. A delivery failure never changes imports, scores, SeenCache marks, or Hermes behavior.

## Interfaces / Contracts

Add `N8nOutboundAdapter.send(envelope) -> DeliveryResult` with injected HTTP transport. POST to the configured endpoint with a bounded timeout; accept only 2xx as acknowledgement and ignore response bodies. Retry transport/5xx failures at most twice after the first attempt (three total), never retry 4xx or malformed configuration. A timeout is `uncertain` (same event may be retried on the next launcher run only if the operator explicitly retries); retries reuse the same ID.

Envelope schema (`schema_version: 1`) is exactly: `event_id`, `run_id`, `event_type` (`automation.run.completed`), `occurred_at`, `result`, and `selected`. `result` contains `searched`, `imported`, `scored`, `failure_count`, and `failures`; counts are non-negative integers, failures contain at most 5 stable labels of 128 characters each. `selected` is null or contains only `title`, `company`, `location`, `score`, `recommendation`, `recommendationLabel`, `jobUrl`, `jobTrailLink`, `source`, and `sourceJobId`; strings are clipped to 200 characters, lists are absent, and only one selected job is sent. The envelope is produced from the existing `NotificationBuilder` allowlist plus stable source identity; no descriptions, notes, prompts, profile/CV data, credentials, or provider payloads cross the boundary. `event_id` is the first 32 hexadecimal characters of SHA-256 over `jobtrail-n8n-v1|run_id|source|sourceJobId` (or `run_id|run` when no job is selected). Thus the same run/job always has the same ID.

Configuration adds an enable flag, endpoint, timeout, and optional bounded retry settings with strict finite defaults. Missing endpoint, disabled flag, or dry-run means no network call. Endpoint and any auth header are configuration-only; secrets are never logged, journaled, or copied into Hermes configuration.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/jobtrail_ai_scorer/n8n_outbound.py` | Create | Small envelope builder and injected HTTP adapter/result contract. |
| `src/jobtrail_ai_scorer/automation.py` | Modify | Expose one stable run context and bounded redacted selected-result data without changing SeenCache or Hermes paths. |
| `src/jobtrail_ai_scorer/run_journal.py` | Modify | Add linked delivery status records while preserving atomic JSONL and 0600 permissions. |
| `scripts/automated-job-search.example.py` | Modify | Wire ordering, config, exit/observability behavior, and dry-run exclusion. |
| `tests/test_n8n_outbound.py`, focused existing tests | Create/modify | Hermetic contract, retry, journal, launcher, and isolation coverage. |
| `docs/runtime-automation.md` | Modify | Scheduler ownership, variables, disable/rollback, envelope, and deferred capabilities. |

## Testing Strategy

Unit tests use fake clocks, injected `httpx.MockTransport`, deterministic IDs, oversized/sensitive fixtures, and response/status permutations. Integration tests verify local journal-before-delivery ordering, delivery classification, disabled mode, and that Hermes/scorer/cache seams are not invoked by n8n delivery. Launcher tests verify dry-run performs no HTTP or journal acknowledgement and local failures remain distinct from delivery failures. Run with `python -m pytest`; no network, Docker, Hermes, WhatsApp, or public service is required.

## Threat Matrix

All listed rows are **N/A**: this change adds no repository selection, Git state, push, PR command, or documentation execution/classification boundary. The adapter uses injected HTTP transport, not shell or subprocess execution; therefore no threat-matrix RED tests are created for those rows.

## Migration / Rollout

Disabled by default. Enable one scheduler only (JobTrail launcher, optionally invoked by n8n as a trigger without n8n scheduling a second run), configure an endpoint, observe delivery journal records, then enable downstream projections. Disable the flag or remove the endpoint to return immediately to local JobTrail plus Hermes. Rollback is code/config reversal; no data migration is required. Telegram callbacks, public HTTPS ingress, authenticated write-back, Google Sheets canonical storage, n8n-owned collection, Docker/systemd changes, and Hermes changes remain deferred.

## Review Budget

Estimated implementation and focused documentation/tests: 220–320 changed lines, below the 400-line budget. Under ask-on-risk, split before implementation if the estimate exceeds 400 lines, if callback/authenticated write-back becomes required, or if delivery ordering requires a new journal schema rather than additive records.

## Open Questions

- None blocking; the deployment owner must supply the n8n endpoint and authentication mechanism without moving Hermes credentials.
