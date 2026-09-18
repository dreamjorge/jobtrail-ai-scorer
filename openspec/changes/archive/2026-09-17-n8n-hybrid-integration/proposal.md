# Proposal: Bounded n8n Hybrid Integration

## Intent
Add a safe, one-way n8n integration around JobTrail’s existing discovery, scoring, journaling, notification, and deduplication pipeline. n8n may project run results to downstream workflows without becoming the source of truth or receiving sensitive profile data.

## Current Gap
The launcher has no bounded, versioned n8n handoff contract, stable external event semantics, or documented scheduler ownership. Run journaling is available but is not yet consistently connected to the canonical launcher. Introducing a second scheduler or deduplication implementation could duplicate imports and notifications.

## Recommendation: Approach 1
Keep JobTrail authoritative for collection, normalization, import, scoring, run identity, journaling, SeenCache deduplication, and outcome classification. Add a small outbound n8n adapter/export using injected HTTP seams. The handoff is versioned, bounded, allowlisted, and redacted; it excludes raw descriptions, prompts, CV/profile data, credentials, and unbounded provider payloads.

Hermes WhatsApp remains an independent local channel. This proposal does not replace, route through, or couple Hermes/WhatsApp to n8n.

Each handoff contains a stable event ID derived from the durable run/job identity, schema version, and delivery metadata. Retries are explicit and idempotent at the handoff boundary; imports and SeenCache marking remain JobTrail-owned. Exactly one scheduler owns the daily run, and `SeenCache` remains the single deduplication authority.

## Scope

**In scope**
- Outbound versioned, redacted n8n handoff and configuration.
- Launcher-to-journal ordering, stable run/event IDs, retry/failure classification, and acknowledgement semantics.
- Documentation naming one scheduler, SeenCache ownership, disable/rollback steps, and secret boundaries.
- Hermetic adapter and integration tests using injected clients/transports.

**Out of scope**
- Telegram callbacks, public HTTPS ingress, authentication, replay-protected write-back, or decision persistence.
- n8n-owned collection, including Indeed HTML, Arbeitnow, or Himalayas ingestion.
- Google Sheets as canonical storage or a new generic webhook framework.
- Changes to Hermes/WhatsApp, Docker, systemd, automatic applications, or private CV/profile transport.

## Affected Areas
`automation.py`, `run_journal.py`, `seen_cache.py`, `notify.py`, `scripts/automated-job-search.example.py`, focused tests, and runtime documentation.

## Acceptance Criteria
- One documented scheduler and one SeenCache dedup authority exist.
- Every handoff is versioned, redacted, bounded, and tied to a stable run/event ID.
- Retries cannot duplicate imports or create ambiguous external events; failures are journaled and observable.
- Existing Hermes WhatsApp behavior remains independent and unchanged.
- `python -m pytest` passes without network, Docker, Hermes, WhatsApp, or public services.
- Telegram callbacks/public HTTPS/authenticated write-back/Google Sheets/n8n-owned collection are explicitly deferred.

## Risks and Rollback
Duplicate delivery, leakage, or journal ordering errors are mitigated by allowlists, stable IDs, SeenCache authority, injected transports, and failure classification. Disable the n8n handoff configuration and revert launcher/adapter/documentation changes; JobTrail’s local pipeline and Hermes channel remain operational.

## Delivery
Use strict TDD later with `python -m pytest`; RDD is off. Delivery remains ask-on-risk. Estimated review size: approximately 180–300 changed lines, below the 400-line budget.
