# Design: n8n Local Compose Validation

## Decision

Add one validation-only Compose project under `validation/n8n/`. It is intentionally separate from the repository-root `docker-compose.yml`, production/systemd configuration, and JobTrail runtime files. The fixture contains only an n8n service and a named disposable volume; it does not add a workflow, scheduler, JobTrail dependency, Hermes dependency, or provider dependency. Existing `tests/test_n8n_outbound.py` remains the source of truth for the bounded handoff envelope and delivery semantics.

The fixture is opt-in through a Compose profile and an explicit operator command. Its image is an interpolation-time required value, its only host binding is loopback, and its persistence is a Compose-managed validation volume that can be removed with the documented cleanup command. Mandatory tests parse and inspect repository-controlled files only; they never invoke Docker, n8n, a network, credentials, or private runtime paths.

## Files and responsibilities

### Create: `validation/n8n/docker-compose.yml`

Compose fixture contract:

- Define exactly one service, `n8n-validation`.
- Set `profiles: [n8n-validation]` so an ordinary `docker compose` invocation cannot start it accidentally.
- Use `image: "${N8N_IMAGE:?N8N_IMAGE must name a reviewed image}"`; there is no `latest` or other fallback.
- Publish the local UI/webhook port as `127.0.0.1:${N8N_HOST_PORT:-5678}:5678`. The default may be overridden only as a host port; the host address remains loopback.
- Use a named volume such as `n8n-validation-data:/home/node/.n8n`; do not use bind mounts, `${HOME}`, `/DATA`, `/AppData`, candidate-profile paths, or any other host path.
- Declare the named volume as local and validation-specific. Do not declare `networks`, `external: true`, `network_mode`, `depends_on`, or links to production services.
- Do not require an `env_file`, credentials, checked-in `.env`, JobTrail URL, Hermes/WhatsApp configuration, provider configuration, or secret environment variables. Any environment entries must be limited to non-secret local validation settings, if n8n requires them.
- Do not add restart policy, production labels, systemd hooks, or a root-level Compose include/extension that could make this service part of production startup.

The Compose file is infrastructure fixture content, not application runtime integration. It must remain usable only after an operator supplies `N8N_IMAGE` explicitly.

### Create: `validation/n8n/README.md`

Document the operator-only flow, including:

1. Purpose and boundary: local webhook acceptance only; n8n acceptance is not downstream completion and does not change JobTrail outcomes.
2. Prerequisites: Docker Compose availability, an operator-reviewed n8n image, and a free local loopback port. No credentials, `.env`, Hermes, WhatsApp, provider, public service, or production JobTrail configuration is needed.
3. Explicit invocation, for example:
   `N8N_IMAGE=<reviewed-image> docker compose --profile n8n-validation -f validation/n8n/docker-compose.yml up -d`
   with an optional `N8N_HOST_PORT` override.
4. Local-only smoke procedure: check the service at the loopback URL and, if an operator has a suitable local webhook configured, submit only a synthetic bounded envelope matching the existing n8n handoff contract. The documentation must not prescribe real jobs, CV/profile data, credentials, provider payloads, or Hermes/WhatsApp calls.
5. Clarify that the smoke flow is optional, operator-run, never a CI gate, and never part of normal JobTrail/systemd startup.
6. Cleanup using the fixture only, including removal of the disposable volume (for example `docker compose --profile n8n-validation -f validation/n8n/docker-compose.yml down -v`), with an explicit warning that the command applies only to this fixture and does not target production Compose files.
7. State that mandatory verification is `python -m pytest tests/test_n8n_compose_validation.py -v` and is static; it does not need Docker or network access.

The README is operational documentation only. It must not add a production configuration path or imply that a successful HTTP response means scoring, importing, notification, or workflow completion.

### Create: `tests/test_n8n_compose_validation.py`

Use `pathlib.Path` relative to the repository root and `yaml.safe_load` (already available through the project dependency) to inspect `validation/n8n/docker-compose.yml`. Tests must read only the fixture, README, and repository-controlled source text. They must not call subprocesses, open sockets, access environment secrets, or load `.env` files.

Focused tests and contracts:

- `test_fixture_is_profile_gated_and_has_one_validation_service`: service set is exactly `{"n8n-validation"}` and the service has the `n8n-validation` profile.
- `test_image_requires_explicit_n8n_image_without_mutable_default`: raw Compose text contains the required `${N8N_IMAGE:?…}` interpolation; it contains no `latest`; the service has no alternate image source.
- `test_every_host_binding_is_loopback_only`: inspect short and long port forms and require host IP `127.0.0.1` (or an equivalent explicit loopback form); reject `0.0.0.0`, wildcard, empty host IP, IPv6 wildcard, and other non-loopback bindings. The test should cover the declared default binding rather than relying only on a string search.
- `test_persistence_is_named_and_validation_scoped`: all service volumes are named-volume declarations targeting the n8n data directory; no source is an absolute path, home expansion, private runtime fragment, or production path; the top-level volume is local and validation-specific.
- `test_fixture_has_no_production_network_or_service_dependencies`: reject `networks`, `network_mode`, `external` network declarations, `depends_on`, `links`, and production service names.
- `test_fixture_has_no_credentials_env_files_or_private_mounts`: reject `env_file`, secret/credential variable names, secret stores, bind mounts, and private-data path fragments. The test should inspect parsed environment keys and raw text so a forbidden dependency cannot hide in either representation.
- `test_documentation_marks_smoke_optional_local_and_non_ci`: README includes explicit statements for operator-only, localhost/loopback-only, optional/non-CI, cleanup, and no production/Hermes/WhatsApp/provider behavior.
- `test_fixture_is_not_referenced_by_production_startup`: the repository root `docker-compose.yml` and known runtime/systemd documentation do not reference `validation/n8n` or `n8n-validation`. This is a narrow text assertion, not a production integration change.

Keep mutation checks deterministic: construct in-memory altered copies of parsed data or text and run small private assertion helpers against those copies where useful. Do not write altered files or launch Compose. The tests should fail with a safety-specific assertion naming the violated property.

### Modify: none of the production files

Do not modify `docker-compose.yml`, systemd/runtime files, `src/jobtrail_ai_scorer/**`, or the existing n8n handoff implementation. Do not change `tests/test_n8n_outbound.py` unless implementation reveals a narrowly missing existing envelope assertion; the preferred design is to leave it untouched.

## Interfaces and data flow

The operator supplies `N8N_IMAGE` and optionally `N8N_HOST_PORT` at invocation time. Compose interpolates the image and maps the selected host port only to loopback. n8n stores its disposable local state in the named validation volume. Any optional smoke request is generated locally with a synthetic, bounded envelope and is sent only to the loopback endpoint. No request is routed through JobTrail, the production Compose project, Hermes, WhatsApp, or an external provider.

The existing JobTrail adapter contract remains unchanged:

- JobTrail owns scheduling, discovery, normalization, importing, scoring, SeenCache, journaling, and local outcomes.
- The envelope is versioned, bounded, redacted, and allowlisted.
- A 2xx webhook response means handoff acceptance only.
- n8n remains disabled by default in production and independent of Hermes/WhatsApp.

The fixture therefore validates the local receiving boundary without becoming a second scheduler, importer, scorer, cache, journal, or integration authority.

## Test strategy and strict TDD cycle

Although the deliverables are configuration, documentation, and static tests rather than production code, apply the requested strict cycle to every contract:

### RED

First create `tests/test_n8n_compose_validation.py` with the focused assertions and the expected fixture/README paths, before creating those files. Run:

```text
python -m pytest tests/test_n8n_compose_validation.py -v
```

Confirm the failures are attributable to missing fixture/documentation or missing safety contract, not import or test-collection errors. Add one test at a time for profile gating, image selection, loopback binding, persistence, forbidden dependencies, documentation, and production separation.

### GREEN

Create only the minimal `validation/n8n/docker-compose.yml` and `validation/n8n/README.md` content needed to satisfy the currently failing assertion. Re-run the focused test after each contract. Do not start Docker and do not add runtime integration. Then run the full hermetic suite:

```text
python -m pytest tests/test_n8n_compose_validation.py tests/test_n8n_outbound.py -v
```

### TRIANGULATE

Validate the same safety decisions from independent views:

1. YAML structure and service/volume/port fields.
2. Raw-text scanning for interpolation, forbidden mounts, env files, mutable image defaults, and production references.
3. Documentation and existing production startup references.

Use in-memory mutations to prove each unsafe regression is rejected: remove the profile, replace the image expression with `latest`, change `127.0.0.1` to `0.0.0.0`, add a bind mount, add a production network, or add a credential variable. These checks remain static and hermetic. Also run the existing envelope tests to ensure the fixture did not alter the handoff contract.

### REFACTOR

After all tests are green, remove duplicated test parsing through small local helpers, clarify assertion messages, and keep the fixture/README wording concise. Re-run the focused static tests and the existing n8n contract tests after every cleanup. Refactoring must not broaden scope or introduce a Docker-dependent test.

## Verification and rollout

Required verification is static pytest only; Docker, n8n, network access, external providers, credentials, `.env`, Hermes, WhatsApp, private profiles, and CV data are prohibited during implementation and CI validation. The optional smoke procedure is documented for a human operator but is not executed by this change or required for acceptance.

Rollout is additive: merge the new validation directory, README, and static test. Existing production Compose/systemd startup and JobTrail behavior remain unchanged because no production file references the fixture. Rollback is deletion of `validation/n8n/` and `tests/test_n8n_compose_validation.py`; no runtime migration or production data cleanup is required.
