## Exploration: n8n hybrid integration

### Current State

JobTrail already has a bounded automation pipeline in `src/jobtrail_ai_scorer/automation.py`: source adapters search, normalized jobs are imported through `POST /api/discover/import`, jobs are scored through an injectable callable or `SCORER_COMMAND`, scores are read back through `GET /api/jobs/{id}`, and one best match may be sent through an injectable notifier/WhatsApp helper. `JobTrailHTTPClient` is the main HTTP extension boundary and deliberately retries search/GET but never retries non-idempotent import. Existing source adapters cover JobSpy plus optional Adzuna, Lever, and Greenhouse; adapters expose injectable `httpx.Client` seams and bounded retry policies.

Deduplication is an operator-path JSON cache keyed by `(source, sourceJobId)`, with atomic writes, `0600` permissions, TTL `2 * hours_old`, and a transaction lock spanning check/import/mark. Run outcomes are represented by `AutomationRun` and can be persisted by `run_journal.record_run` as atomic JSONL with schema version, counts, failure labels, notification classification, timestamps, and backend URL source. The journal module exists and is tested, but the canonical launcher currently prints a summary and does not call `record_run`; this is an important integration gap.

Notifications are currently outbound-only through a configured subprocess (`WHATSAPP_NOTIFY_COMMAND`). `NotificationBuilder` provides an allowlist, redaction, JobTrail link, recommendation label, and run ID. No inbound webhook, callback endpoint, Telegram inline-button model, Google Sheets sink, or generic outbound webhook adapter exists in this repository. The only HTTP APIs modeled are JobTrail search/import/read, scorer provider HTTP, and public source adapters. The external n8n evidence says n8n schedules daily runs, collects Indeed HTML plus Arbeitnow/Himalayas APIs, statically filters/deduplicates, sends Telegram inline buttons, stores interested jobs in Google Sheets, and requires public HTTPS for Telegram callback webhooks.

Deployment is operator-controlled: `scripts/automated-job-search.example.py` is the canonical scheduler entry point; discovery probes localhost then Docker IP and fails closed. There are no committed systemd unit/timer files. Docker packaging installs the scorer CLI only; `docker-compose.yml` runs the scorer service and mounts local config/profile examples. Runtime installation is explicitly staged/absolute-path based and guarded by `--yes`. Tests use fake gateways, injectable clients, `httpx.MockTransport`, in-process HTTP stubs, fake scorer/notifier callables, and monkeypatched launcher imports. Required later runner is `python -m pytest`.

### Affected Areas
- `src/jobtrail_ai_scorer/automation.py` — orchestration boundary, injectable gateway/scorer/notifier, source normalization, dedup and notification behavior.
- `src/jobtrail_ai_scorer/run_journal.py` — existing durable run contract; likely integration point for n8n exports or reconciliation.
- `src/jobtrail_ai_scorer/seen_cache.py` — existing dedup identity and TTL contract that must not diverge from any n8n-side dedup.
- `src/jobtrail_ai_scorer/notify.py` — safe outbound payload allowlist and stable run/job links.
- `scripts/automated-job-search.example.py` — scheduler/runtime boundary, discovery, exit codes, stdout/stderr contract, and likely journal hook.
- `src/jobtrail_ai_scorer/sources/*.py` — existing source adapter pattern; external Arbeitnow/Himalayas/Indeed collection should not silently bypass normalization and safety boundaries.
- `tests/test_automation.py`, `tests/test_automation_e2e.py`, `tests/test_run_journal.py`, `tests/test_seen_cache.py`, `tests/test_automated_job_search_launcher.py` — primary hermetic seams and acceptance evidence.
- `README.md`, `docs/runtime-automation.md`, `docs/plans/*` — documentation convention: dated design/implementation plans plus explicit environment, safety, and test contracts.

### Approaches

1. **n8n as scheduler/notification adapter around JobTrail** — Keep JobTrail discovery, import, scoring, dedup, and journal authoritative. Add a bounded outbound n8n webhook sink or launcher export carrying the existing redacted summary/run metadata; let n8n schedule and fan out to Telegram/Sheets. Keep Telegram callback handling outside JobTrail unless a later proposal proves the need.
   - Pros: smallest contract change; preserves existing dedup, scoring, redaction, and test seams; avoids duplicating Indeed/API collection; no public callback requirement for one-way notifications.
   - Cons: n8n cannot provide native inline-button callbacks without a separate public HTTPS receiver; Google Sheets remains an external projection, not JobTrail state.
   - Effort: Medium.

2. **n8n owns collection and JobTrail remains scoring/import backend** — n8n gathers Indeed/Arbeitnow/Himalayas, filters/deduplicates, then calls a documented JobTrail ingestion/score boundary. JobTrail must define source identity mapping, idempotency, payload validation, and failure/journal semantics for externally collected jobs.
   - Pros: close to the supplied n8n-job-radar shape; n8n can use its native Telegram and Sheets integrations.
   - Cons: duplicates source adapters and dedup truth; risks divergent filtering, identity, retries, and sensitive-field handling; requires a durable ingestion contract not currently present.
   - Effort: High.

3. **Full bidirectional hybrid** — JobTrail owns canonical jobs/scoring/journal; n8n receives summaries and Telegram callbacks through a public HTTPS webhook, then writes interest decisions back through a new authenticated JobTrail endpoint or an explicit external decision store.
   - Pros: supports the desired inline-button interaction and keeps scoring canonical if the write-back contract is sound.
   - Cons: largest security/deployment surface; requires authentication, replay/idempotency, callback validation, decision schema, public ingress, and reconciliation when n8n or Telegram is unavailable.
   - Effort: High.

### Recommendation

Recommend Approach 1 for the initial bounded change: expose the existing safe run result/journal as an n8n-consumable outbound contract, schedule the canonical launcher from either the current operator scheduler or n8n (one scheduler only), and use n8n for Telegram/Sheets projections. Explicitly defer Telegram callback write-back and n8n-owned source collection. If inline buttons are mandatory in this change, select Approach 3 only after treating public HTTPS, authentication, replay protection, and decision persistence as first-class requirements rather than embedding callback logic in the notifier subprocess.

### Scope / Non-scope

**In scope:** one canonical daily-run ownership model; an outbound n8n handoff contract based on allowlisted notification/journal data; stable run/job/source identities; failure and retry semantics; dedup ownership; scheduler/deployment documentation; hermetic HTTP and callback-free tests; redaction and no-secret guarantees.

**Out of scope for the bounded first slice:** replacing existing source adapters with Indeed HTML/Arbeitnow/Himalayas collection; Google Sheets as canonical storage; arbitrary webhook execution; Telegram callback decision write-back; public ingress provisioning; Docker/systemd/WhatsApp/Hermes runtime changes; automatic job application; private CV/profile transport.

### Risks

- Two schedulers or two dedup implementations could create duplicate imports and notifications; exactly one scheduler and one dedup authority must be named.
- An n8n webhook must not receive raw descriptions, notes, prompts, CV/profile data, credentials, or unbounded provider payloads; reuse the `NotificationBuilder` allowlist and journal failure labels.
- Telegram inline callbacks require publicly reachable HTTPS and authenticated/replay-safe handling; outbound webhook delivery alone does not satisfy this requirement.
- n8n retries can duplicate non-idempotent effects unless the handoff has a stable run/event identity and explicit acknowledgement semantics.
- Current launcher does not appear to persist `run_journal.record_run`; adding an n8n handoff without first defining journal ordering can produce untraceable external events.
- External source semantics differ from JobTrail adapters; source identity, freshness, static filtering, and HTML/API normalization must not drift silently.

### Acceptance Criteria Candidates

- A daily run has one authoritative scheduler, one `(source, sourceJobId)` dedup contract, and one durable run ID visible in the journal and n8n handoff.
- The handoff is bounded, redacted, versioned, and contains enough data to render Telegram/Sheets projections without CV/profile/prompt/credential leakage.
- Delivery failures are classified without crashing or falsely marking the JobTrail run successful; retries cannot duplicate imports or ambiguous decisions.
- Existing search/import/score/notify behavior and `python -m pytest` hermetic suites remain green; new HTTP seams use injected clients/transports and never require public network, Docker, Hermes, WhatsApp, or systemd.
- Documentation records scheduler ownership, endpoint/authentication/replay behavior (if any), rollback/disable procedure, environment variables, and explicit non-scope.

### Review-Budget Estimate

Initial Approach 1: approximately 180–300 changed lines including a small adapter/contract, launcher wiring, tests, and focused documentation; likely within the 400-line review budget. Approach 3: approximately 350–600+ lines before deployment manifests and security tests, therefore at or above the budget and requiring an explicit ask-on-risk decision. Approach 2 is likely 300–500 lines and carries higher semantic risk despite similar line count.

### Ready for Proposal

Yes. Proposal should choose Approach 1 unless inline Telegram callbacks are a hard requirement; in that case pause on the public-HTTPS/authentication/replay gate and explicitly authorize the larger Approach 3 scope.
