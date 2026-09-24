# Tasks: n8n Local Compose Validation

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 280–360 additions across 3 new files |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

## Scope and boundaries

- Edit only `validation/n8n/docker-compose.yml`, `validation/n8n/README.md`, `tests/test_n8n_compose_validation.py`, and this task artifact.
- Do not modify `docker-compose.yml`, systemd/runtime files, `src/jobtrail_ai_scorer/**`, `tests/test_n8n_outbound.py`, credentials, `.env` files, private data, or production configuration.
- Do not start Docker or n8n, invoke external services, open sockets, inspect Hermes/WhatsApp/providers, or use private runtime paths.
- Mandatory verification is static pytest using repository-controlled files only.

## RED — establish failing hermetic contracts first

- [x] Task 1: Add focused static contract tests before fixtures

**Files:**
- Create: `tests/test_n8n_compose_validation.py`
- Read-only references: `validation/n8n/docker-compose.yml`, `validation/n8n/README.md`, `docker-compose.yml`, and known systemd/runtime documentation

Write tests first, before creating the fixture or README, with small local helpers for repository-root paths, YAML loading, host-port parsing, and safety assertions. Cover one behavior per test:

- exactly one `n8n-validation` service and the `n8n-validation` profile;
- required `${N8N_IMAGE:?…}` interpolation with no `latest` fallback or alternate image source;
- every host binding explicitly restricted to `127.0.0.1` (reject wildcard, empty, public, and non-loopback forms);
- named validation-scoped persistence only, targeting the n8n data directory and no host/private path;
- no networks, external networks, `network_mode`, `depends_on`, `links`, or production service dependency;
- no `env_file`, credentials/secrets, private mounts, or forbidden private-data path fragments;
- README statements for operator-only, loopback/localhost, optional/non-CI use, cleanup, and no production/Hermes/WhatsApp/provider behavior;
- no reference to the validation fixture from root production Compose or runtime/systemd startup text.

Use `yaml.safe_load` and raw-text checks. Keep tests hermetic: no subprocesses, sockets, environment-secret reads, `.env` loads, or Docker calls. Add deterministic in-memory mutation checks for unsafe profile, image, binding, mount, network, and credential regressions where the private helpers make that practical. <!-- sdd-owner: implementation -->

- [x] Task 2: Verify the RED state for the focused suite

Run from the repository root:

```text
python -m pytest tests/test_n8n_compose_validation.py -v
```

Expected result: collection succeeds and the contract tests fail because `validation/n8n/docker-compose.yml` and `validation/n8n/README.md` do not yet exist, or because the missing fixture contract is reported. Fix test typos or collection errors only; do not add fixture files before the intended failures are observed. <!-- sdd-owner: implementation -->

## GREEN — implement the minimum local validation surface

- [x] Task 3: Implement the profile-gated Compose fixture

**Files:**
- Create: `validation/n8n/docker-compose.yml`

Add exactly one service, `n8n-validation`, with `profiles: [n8n-validation]` and image expression `"${N8N_IMAGE:?N8N_IMAGE must name a reviewed image}"`. Publish only `127.0.0.1:${N8N_HOST_PORT:-5678}:5678`. Use one Compose-managed, local, validation-scoped named volume mounted at `/home/node/.n8n`. Do not add networks, external resources, dependencies, host bind mounts, `env_file`, credentials, secrets, production labels, restart policy, JobTrail/Hermes/WhatsApp/provider settings, or root Compose includes. Keep the file free of `latest` and production/private path references. <!-- sdd-owner: implementation -->

- [x] Task 4: Run the fixture contract tests and correct only fixture contract failures

Run:

```text
python -m pytest tests/test_n8n_compose_validation.py -v
```

Expected result: Compose-structure tests pass for the new fixture; README and production-separation tests may still fail because those files/text have not been added. If a Compose test fails, correct only the minimal YAML contract needed for that failure. Do not invoke Docker, n8n, or any network. <!-- sdd-owner: implementation -->

- [x] Task 5: Add operator-only local validation documentation

**Files:**
- Create: `validation/n8n/README.md`

Document the boundary and explicit command using `N8N_IMAGE=<reviewed-image> docker compose --profile n8n-validation -f validation/n8n/docker-compose.yml up -d`, with an optional loopback host-port override. State that the smoke check is optional, operator-run, localhost/loopback-only, non-CI, and limited to synthetic bounded handoff acceptance. State that no credentials, `.env`, private profile/CV data, production JobTrail configuration, Hermes, WhatsApp, providers, public services, or real job payloads are needed. Include disposable-volume cleanup using the validation Compose file only, and explicitly say webhook acceptance is not scoring, importing, notification, workflow, or downstream completion. Include the static verification command `python -m pytest tests/test_n8n_compose_validation.py -v`. <!-- sdd-owner: implementation -->

- [x] Task 6: Verify GREEN with focused and existing hermetic tests

Run:

```text
python -m pytest tests/test_n8n_compose_validation.py -v
python -m pytest tests/test_n8n_compose_validation.py tests/test_n8n_outbound.py -v
```

Expected result: both commands pass without Docker, network access, credentials, `.env`, Hermes, WhatsApp, providers, or private runtime inspection. The existing outbound tests must remain unchanged and continue to prove the bounded/redacted envelope and acknowledgement semantics. <!-- sdd-owner: implementation -->

## TRIANGULATE — prove unsafe regressions are rejected statically

- [x] Task 7: Exercise independent parsed, raw-text, and documentation safety views

**Files:**
- Modify: `tests/test_n8n_compose_validation.py`

Review the focused tests so each safety property is checked from the appropriate independent view: parsed YAML for services, profiles, ports, volumes, and dependencies; raw Compose text for required interpolation and forbidden textual paths/secrets; README/root runtime text for operator boundaries and production separation. Add or refine in-memory mutation assertions so changing `127.0.0.1` to `0.0.0.0`, replacing the required image expression with `latest`, removing the profile, adding a bind mount, adding a production network, or adding a credential key produces a safety-specific assertion. Never write mutations to disk. <!-- sdd-owner: implementation -->

- [x] Task 8: Run triangulation and repository regression checks

Run:

```text
python -m pytest tests/test_n8n_compose_validation.py -v
python -m pytest tests/test_n8n_compose_validation.py tests/test_n8n_outbound.py tests/test_automation.py -v
```

Expected result: all selected tests pass statically. No command may start Docker or contact any service. If a failure appears in an unrelated production/runtime area, stop and report it rather than changing out-of-scope files. <!-- sdd-owner: implementation -->

## REFACTOR — simplify without broadening scope

- [x] Task 9: Refactor local test helpers and fixture wording after green

**Files:**
- Modify: `tests/test_n8n_compose_validation.py`
- Modify: `validation/n8n/docker-compose.yml`
- Modify: `validation/n8n/README.md`

After green, remove duplicated parsing or assertion setup, clarify safety-specific failure messages, and keep the Compose/README content concise. Preserve all explicit opt-in, required-image, loopback, disposable-volume, no-private-dependency, no-production-reference, non-CI, and acceptance-only guarantees. Do not introduce Docker-dependent tests or production integration. <!-- sdd-owner: implementation -->

- [x] Task 10: Perform final static verification and review the allowed diff

Run:

```text
python -m pytest tests/test_n8n_compose_validation.py tests/test_n8n_outbound.py -v
```

Then review that the only implementation files are `validation/n8n/docker-compose.yml`, `validation/n8n/README.md`, and `tests/test_n8n_compose_validation.py`; confirm no Docker command, external service access, credentials, `.env`, private data, production runtime change, or n8n handoff change was introduced. Expected result: focused validation and existing outbound contract tests pass, with total implementation changes remaining below the 400-line review budget. <!-- sdd-owner: implementation -->
