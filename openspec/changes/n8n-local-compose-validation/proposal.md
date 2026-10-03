# Proposal: Local n8n Compose Validation Fixture

## Intent
Add an explicitly opt-in, local-only Docker Compose validation surface for the existing JobTrail-to-n8n handoff. The fixture and hermetic contract tests should let maintainers verify the local webhook boundary without changing production deployment, scheduler ownership, data boundaries, or Hermes/WhatsApp behavior.

## Current-State Gap
The bounded n8n handoff implementation and contract tests exist, but there is no isolated local Compose fixture that demonstrates webhook acceptance or statically proves the safety properties of a local n8n validation surface. Operators therefore lack a repeatable local validation path, while adding an ordinary Compose service could accidentally expose ports, introduce mutable images, require credentials, or become part of production startup.

## Recommendation
Create a clearly named validation fixture under a local validation directory, enabled only through an explicit Compose profile or command. Require an operator-supplied `N8N_IMAGE` value rather than silently selecting `latest`; bind any host port to loopback; use disposable local persistence; avoid host/private-path mounts, production networks, credentials, and checked-in `.env` requirements.

Add mandatory static tests that parse or inspect the Compose definition without invoking Docker. These tests must reject unsafe host bindings, production network references, private/host mounts, implicit mutable image defaults, credential requirements, and accidental default activation. They should also verify that the fixture remains separate from production/systemd startup and does not alter the existing bounded envelope contract.

An optional operator-run localhost smoke flow may prove that a selected local n8n image accepts the handoff webhook. It is explicitly separate from mandatory static tests, is not required by CI, does not contact Hermes, WhatsApp, providers, or public services, and must not modify production JobTrail configuration. The source of truth for payload safety remains the existing hermetic envelope contract tests; the fixture is not a second scheduler, importer, scorer, SeenCache, journal, or integration authority.

## Scope

### In scope
- A local-only Docker Compose validation fixture in a clearly named validation directory.
- Explicit opt-in activation, with production and systemd startup unaffected by its presence.
- Required image selection through `N8N_IMAGE`, with no silent `latest` fallback.
- Loopback-only host exposure, disposable local persistence, and no private host mounts or production networks.
- Static/hermetic tests that inspect the Compose definition and enforce the safety and opt-in contract without starting Docker.
- Documentation for the operator-only localhost smoke flow, including prerequisites, cleanup expectations, and its non-CI/non-production status.
- Tests or fixture wiring needed to demonstrate acceptance of a bounded handoff without expanding the handoff payload or ownership model.

### Out of scope
- Starting or stopping Docker, n8n, Hermes, WhatsApp, systemd, external providers, or public services during automated validation.
- Any production n8n activation, deployment, Docker Compose change, systemd change, or `/DATA/AppData/jobtrail` modification.
- Credentials, `.env` files, private profile/CV data, provider payloads, or secret/private-path mounts.
- A second scheduler, n8n-owned collection, importing, scoring, SeenCache, journaling, or downstream decision authority.
- Changes to JobTrail’s scheduler, scorer, local pipeline, bounded/redacted envelope, or Hermes/WhatsApp channel.
- Public ingress, callbacks, write-back, Google Sheets, or other deferred n8n capabilities.

## Affected Areas
- New local validation Compose fixture and narrowly scoped operator documentation.
- New hermetic static validation tests and any test-only fixture data required to inspect the Compose contract.
- Existing n8n handoff contract tests only if a focused assertion is needed to preserve the bounded/redacted envelope boundary.
- No production runtime or deployment files should be modified.

## Product and Technical Rules
- JobTrail remains the sole owner of scheduling, discovery, normalization, importing, scoring, SeenCache, journaling, and local outcomes.
- n8n acceptance means only that the local webhook accepted the bounded handoff; it does not imply downstream completion.
- The envelope remains versioned, bounded, redacted, and allowlisted, excluding raw descriptions, prompts, CV/profile data, credentials, and unbounded provider payloads.
- n8n delivery remains disabled by default in production and independent of Hermes/WhatsApp.
- Static tests must be hermetic and must not require Docker or network access.
- The fixture may be run only by an operator who explicitly opts in and selects a reviewed image.
- End-to-end JobTrail delivery validation is independent of the fixture and should not be used to mask the currently known `invalid_score` blocker.

## Success Criteria
- A reviewer can identify the fixture as local validation-only and confirm that normal production startup does not activate it.
- Static tests pass without Docker, n8n, network access, credentials, `.env`, Hermes, WhatsApp, or provider access.
- Static tests fail for non-loopback host exposure, production network coupling, private/host mounts, implicit mutable image selection, credential requirements, or default activation.
- The fixture requires an explicit `N8N_IMAGE` and uses only disposable local persistence.
- The optional localhost smoke procedure is clearly non-CI, operator-run, local-only, and independent of production JobTrail configuration.
- Existing scheduler authority, bounded/redacted envelope guarantees, disabled-by-default behavior, Hermes independence, and no-private-data rules remain unchanged.

## Risks and Mitigations

- **Accidental exposure:** Bind host ports to loopback only and enforce this statically; avoid public ingress and production networks.
- **Mutable or unreviewed runtime:** Require `N8N_IMAGE` explicitly and document that operators choose a reviewed image.
- **Credential or private-data leakage:** Use no checked-in credentials, `.env` requirement, private mounts, or profile/CV inputs; keep payload assertions bounded and redacted.
- **CI becoming Docker-dependent:** Keep all mandatory tests static; treat localhost smoke as optional and operator-run only.
- **Fixture becoming production infrastructure:** Use a validation-only directory/profile and test that it is not part of production/systemd startup.
- **Confusing acceptance with business completion:** Document that webhook acceptance is only handoff acknowledgement and does not change JobTrail ownership or outcomes.
- **Scoring instability obscuring validation:** Validate the fixture and contract independently; do not make this change responsible for repairing the separate scorer blocker.

## Rollback
Remove or disable the validation-only fixture, its static tests, and its documentation. No production scheduler, n8n configuration, Hermes/WhatsApp behavior, or JobTrail local pipeline should require the fixture, so rollback must leave production behavior unchanged. If an operator starts the optional fixture, stopping and removing its disposable local resources is sufficient; no production data or configuration should be involved.

## Verification Boundary
Mandatory verification is static and hermetic: parse/inspect the Compose definition and run the focused contract tests without launching Docker or contacting any service. The localhost smoke flow is an explicitly optional manual check for an operator with a selected image; it must never be a CI gate or prerequisite for the proposal’s acceptance.
