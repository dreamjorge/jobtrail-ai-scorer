# n8n Local Compose Validation Specification

## Purpose

Provide a local-only validation surface for the existing JobTrail-to-n8n handoff without changing production startup, runtime ownership, data boundaries, or Hermes/WhatsApp behavior.

## Requirements

### Requirement: Explicit opt-in local activation

The validation fixture MUST be inactive unless an operator explicitly opts into its local validation command or profile. Its presence MUST NOT activate n8n delivery, alter production startup, or become part of systemd startup by default.

#### Scenario: Default project startup

- GIVEN the repository is used without an explicit local validation opt-in
- WHEN normal JobTrail or systemd startup is performed
- THEN the validation fixture is not started and production behavior is unchanged

#### Scenario: Explicit validation opt-in

- GIVEN an operator intentionally selects the validation fixture
- WHEN the operator invokes the documented local validation entry point
- THEN only the local validation surface is eligible to run

### Requirement: Required reviewed image selection

The validation fixture MUST require an operator-supplied `N8N_IMAGE` value before it can be used. It MUST NOT silently select `latest`, another mutable default, or an unreviewed image when the value is absent.

#### Scenario: Image is not supplied

- GIVEN `N8N_IMAGE` is absent or empty
- WHEN the validation configuration is inspected or invoked
- THEN validation fails clearly before a service can start

#### Scenario: Image is supplied

- GIVEN an operator explicitly supplies a reviewed `N8N_IMAGE` value
- WHEN the validation configuration is inspected
- THEN the selected image is the only image source required by the fixture

### Requirement: Loopback-only host exposure

Any host-exposed validation endpoint MUST bind exclusively to loopback addresses. The fixture MUST NOT expose n8n on all interfaces, a public interface, or an otherwise non-loopback host address.

#### Scenario: Safe local binding

- GIVEN the fixture declares host access for local validation
- WHEN its host bindings are inspected
- THEN every host binding is restricted to loopback

#### Scenario: Unsafe host binding

- GIVEN a fixture definition contains a wildcard, public, or non-loopback host binding
- WHEN hermetic static validation runs
- THEN validation fails and identifies the unsafe exposure

### Requirement: Disposable local persistence

The validation fixture MUST use persistence that is disposable and local to the validation run. It MUST NOT require or modify production JobTrail data, durable production volumes, or private host data.

#### Scenario: Disposable cleanup

- GIVEN an operator completes a local validation run
- WHEN the documented cleanup is performed
- THEN the fixture's persisted state can be removed without affecting JobTrail production data or configuration

#### Scenario: Production data isolation

- GIVEN the fixture configuration is inspected
- WHEN persistence declarations are evaluated
- THEN no production data location or private host path is required or referenced

### Requirement: No private mounts, credentials, or production networks

The validation fixture MUST NOT require credentials, checked-in `.env` content, private profile or CV data, host/private-path mounts, or production network attachments. Static validation MUST reject any such dependency.

#### Scenario: Hermetic safe configuration

- GIVEN the fixture configuration is inspected without credentials or private runtime files
- WHEN static validation runs
- THEN the configuration is valid without secret values, `.env` files, private mounts, or production network access

#### Scenario: Unsafe dependency is introduced

- GIVEN a fixture revision adds a credential requirement, private or host mount, or production network reference
- WHEN hermetic static validation runs
- THEN validation fails and identifies the forbidden dependency

### Requirement: Hermetic static validation

Mandatory automated validation MUST inspect the fixture and its declared safety contract without starting Docker or n8n, requiring network access, contacting Hermes, WhatsApp, providers, or any external service, or reading credentials or private runtime data.

#### Scenario: Offline validation

- GIVEN Docker is unavailable and no external services or credentials are present
- WHEN the mandatory static validation suite runs
- THEN it can parse and evaluate the fixture using repository-controlled inputs only

#### Scenario: Unsafe contract regression

- GIVEN a fixture revision loses explicit opt-in, required image selection, loopback-only exposure, disposable persistence, or forbidden-dependency isolation
- WHEN the mandatory static validation suite runs
- THEN the suite fails before any runtime service is started

### Requirement: Optional localhost smoke documentation

The project MUST document an optional operator-run localhost smoke flow that uses an explicitly selected image and local resources only. The documentation MUST state prerequisites, cleanup expectations, that the flow is not a CI requirement, and that it MUST NOT alter production JobTrail configuration or contact Hermes, WhatsApp, providers, or public services.

#### Scenario: Operator follows the optional flow

- GIVEN an operator has explicitly opted in and selected a reviewed image
- WHEN the operator follows the documented localhost smoke flow
- THEN the check is limited to local webhook acceptance and its disposable resources can be cleaned up

#### Scenario: CI execution boundary

- GIVEN mandatory CI or hermetic validation is run
- WHEN the validation suite executes
- THEN the optional localhost smoke flow is not required and no Docker or service startup is performed

### Requirement: Preserve JobTrail and Hermes/envelope boundaries

The validation fixture MUST preserve the existing handoff contract: JobTrail remains the sole owner of scheduling, discovery, normalization, importing, scoring, SeenCache, journaling, and local outcomes; the handoff envelope remains versioned, bounded, redacted, and allowlisted; and n8n acceptance MUST mean only webhook handoff acceptance. The fixture MUST remain independent of Hermes and WhatsApp and MUST NOT introduce a second scheduler, importer, scorer, SeenCache, journal, or integration authority.

#### Scenario: Bounded handoff validation

- GIVEN a local validation checks a JobTrail handoff
- WHEN the handoff is presented to the fixture
- THEN only the existing bounded and redacted envelope contract is evaluated and no additional private or unbounded payload is required

#### Scenario: Independent channels and ownership

- GIVEN n8n validation is enabled, unavailable, or successful
- WHEN JobTrail and Hermes/WhatsApp behavior are considered
- THEN JobTrail ownership and local outcomes remain unchanged and Hermes/WhatsApp behavior remains independent

#### Scenario: Acceptance is not completion

- GIVEN the local webhook accepts a handoff
- WHEN the validation result is reported
- THEN it reports handoff acceptance only and does not claim downstream completion or change JobTrail decisions
