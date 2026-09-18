# n8n Hybrid Integration Specification

## Purpose

Define a bounded, one-way JobTrail-to-n8n handoff. JobTrail remains authoritative for scheduling, discovery, import, scoring, SeenCache deduplication, and journaling. Hermes WhatsApp remains independent.

## Requirements

### Requirement: Canonical scheduler and ownership

JobTrail MUST own the sole daily scheduler, discovery, normalization, import, scoring, and SeenCache authority. n8n MUST NOT collect jobs or schedule a second run.

#### Scenario: Authoritative run
- GIVEN the daily automation is enabled
- WHEN the schedule fires
- THEN JobTrail performs the local pipeline and owns its outcome

### Requirement: Versioned bounded redacted envelope

Each handoff MUST declare a schema version and contain only bounded, allowlisted summary and outcome fields. It MUST exclude raw descriptions, prompts, CV/profile data, credentials, and unbounded provider payloads.

#### Scenario: Safe handoff
- GIVEN a run has eligible results
- WHEN its envelope is prepared
- THEN it contains bounded redacted data sufficient for downstream projection

### Requirement: Stable identity and acknowledgement

Each event MUST use a stable identity derived from durable run and job identity. Retries MUST reuse that identity. Acknowledgement MUST mean only that n8n accepted the event, not that deferred downstream actions occurred.

#### Scenario: Retry and acceptance
- GIVEN delivery is uncertain or temporarily fails
- WHEN the event is retried and later accepted
- THEN its identity remains unchanged and JobTrail records acceptance without changing job decisions

### Requirement: Journal and launcher ordering

The launcher MUST establish the run identity and record local run outcome before or atomically with the related delivery outcome. Journal records MUST link run, event, and delivery classifications.

#### Scenario: Traceable completion
- GIVEN discovery and scoring finish
- WHEN handoff processing completes
- THEN the journal links the run and event with their outcome classifications

### Requirement: Failure behavior

Delivery failure MUST be classified and observable without falsely reporting acknowledgement. It MUST NOT erase or corrupt the local result, and local failure MUST remain distinct from downstream failure.

#### Scenario: n8n unavailable
- GIVEN local work completes but n8n is unreachable
- WHEN the launcher finishes
- THEN local outcome is retained and delivery is recorded as unacknowledged failure

### Requirement: Dry-run exclusion

Dry-run MUST NOT import jobs, mark SeenCache entries, send handoffs, or record delivery acknowledgements. It MAY display a bounded candidate envelope locally.

#### Scenario: Dry run
- GIVEN dry-run mode is enabled
- WHEN candidates are discovered
- THEN no external handoff or durable import/deduplication side effect occurs

### Requirement: Hermes independence

n8n delivery MUST remain independent of Hermes and WhatsApp. Enabling, disabling, retrying, or failing delivery MUST NOT alter or require Hermes behavior.

#### Scenario: Separate channels
- GIVEN Hermes is configured or unavailable
- WHEN n8n delivery succeeds or fails
- THEN Hermes behavior and configuration remain unchanged

### Requirement: Disable and rollback

Operators MUST be able to disable n8n delivery without disabling the local JobTrail pipeline. Disabled delivery MUST make no network handoff and MUST preserve local journaling and Hermes operation.

#### Scenario: Disabled handoff
- GIVEN delivery is disabled by configuration
- WHEN a scheduled run executes
- THEN local processing completes and the journal records delivery as disabled

### Requirement: Private-data and secret boundaries

Only explicitly allowlisted public-safe summaries MAY cross the handoff. Secrets, credentials, private profile/CV content, prompts, and raw provider records MUST be absent from payloads, logs, and journal data.

#### Scenario: Sensitive input
- GIVEN private data or credentials are present
- WHEN an envelope and outcome are recorded
- THEN those values are absent from transmitted and observable records

### Requirement: Deferred capabilities

This slice MUST NOT implement Telegram callbacks, public HTTPS ingress, authenticated write-back, Google Sheets as canonical storage, n8n-owned collection, or changes to Docker, systemd, or Hermes.

#### Scenario: Deferred request
- GIVEN a downstream request requires a deferred capability
- WHEN the one-way handoff is used
- THEN no callback, write-back, collection, or deferred runtime integration is performed
