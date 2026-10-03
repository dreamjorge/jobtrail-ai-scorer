# Exploration: n8n local Compose validation

## Scope

Add an explicitly opt-in, local-only Docker Compose validation surface for the already-implemented JobTrail-to-n8n handoff. The production scheduler, scoring, SeenCache, journal, Hermes/WhatsApp channel, and n8n disabled-by-default behavior remain unchanged.

The implementation base is the current worktree `feat/n8n-hybrid-integration`, which contains the merged handoff implementation and tests plus the uncommitted backend-discovery fix. No private runtime files, credentials, CV/profile data, or `.env` content are part of this change.

## Existing contract

The canonical handoff contract in `openspec/specs/n8n-handoff/spec.md` already establishes:

- JobTrail owns scheduling, discovery, importing, scoring, SeenCache, and journaling.
- The outbound envelope is versioned, bounded, redacted, and allowlisted.
- Stable event identities are reused across retries.
- n8n delivery is disabled by default and independent of Hermes/WhatsApp.
- Delivery acknowledgement means only that n8n accepted the handoff.
- Dry-run and hermetic tests must not contact services or providers.
- Telegram callbacks, public ingress, write-back, Google Sheets, n8n-owned collection, and production Docker changes are out of scope.

Relevant implementation and tests are in `src/jobtrail_ai_scorer/n8n_outbound.py`, `src/jobtrail_ai_scorer/automation.py`, `tests/test_n8n_outbound.py`, `tests/test_automation.py`, and `tests/test_automated_job_search_launcher.py`.

## Recommended validation shape

1. Add a local-only Compose fixture under a clearly named validation directory.
2. Require an explicit image value rather than silently using `latest`.
3. Bind any host port to loopback only; do not join production networks or mount host/private paths.
4. Use disposable local persistence and no checked-in credentials or `.env` requirement.
5. Keep the fixture opt-in via a Compose profile or explicit command; it must not be part of production/systemd startup.
6. Add hermetic static tests that parse/inspect the Compose definition and verify the safety properties without invoking Docker.
7. Add a local workflow fixture only if needed to prove webhook acceptance; the source of truth remains the bounded envelope contract tests.
8. Make live localhost smoke optional and operator-run only. It must not be required by CI, must not contact Hermes/WhatsApp/providers, and must not alter production JobTrail configuration.

## Risks and decisions

- A default n8n image tag could be mutable; requiring `N8N_IMAGE` avoids inventing a version and makes the operator choose a reviewed image.
- A host bind of `0.0.0.0` or a production network could expose the local fixture; tests must reject both.
- Docker-based tests would violate hermeticity; all automated checks remain static.
- The scorer currently has an independent `invalid_score` blocker. The n8n fixture can be validated independently, but end-to-end JobTrail delivery should wait until scoring is healthy.
- No n8n compose file currently exists in the implementation worktree. This change should add only the validation fixture and docs/tests, not modify production deployment.

## Non-goals

- No activation of n8n in `/DATA/AppData/jobtrail`.
- No credentials, private mounts, Hermes integration, WhatsApp changes, public ingress, callbacks, or second scheduler.
- No Docker start/stop commands in automated tests.

## Safety constraints

No Docker, n8n, Hermes, WhatsApp, external providers, credentials, `.env`, CV, or private profile inspection during SDD planning or hermetic verification.
