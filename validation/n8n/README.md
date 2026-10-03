# Local n8n validation

This is an optional, operator-only localhost smoke check for the bounded JobTrail-to-n8n handoff. This local fixture validates envelope shape, expiry, and replay behavior in an operator-only synthetic environment; it does not authenticate tokens cryptographically and must not be exposed publicly. It is not a production service, is not part of normal startup or systemd, and is explicitly non-CI.

## Local-only procedure

Prerequisites are Docker Compose, an operator-reviewed n8n image, and a free loopback port. No credentials, `.env` file, private profile or CV data, production JobTrail configuration, Hermes, WhatsApp, provider, public service, or real job payload is needed.

Start the explicitly opted-in validation profile with the reviewed image:

```sh
N8N_IMAGE=<reviewed-image> docker compose --profile n8n-validation -f validation/n8n/docker-compose.yml up -d
```

An operator may choose a different local port while retaining loopback-only exposure:

```sh
N8N_IMAGE=<reviewed-image> N8N_HOST_PORT=5679 docker compose --profile n8n-validation -f validation/n8n/docker-compose.yml up -d
```

The optional smoke check may inspect the service at `http://localhost:${N8N_HOST_PORT:-5678}` and, only when a suitable local webhook is already configured, submit a synthetic bounded handoff envelope. It must remain localhost/loopback-only and operator-run. Webhook acceptance is not scoring, importing, notification, workflow execution, downstream completion, or a change to JobTrail outcomes.

Do not use real jobs, descriptions, CV/profile data, credentials, provider payloads, Hermes, WhatsApp, or public services in this check.

## Synthetic feedback callback

The workflow at `validation/n8n/workflows/job-feedback.json` is operator-imported only; it is inactive by default and has no schedule or public URL. Import it manually in the local n8n UI, then use only `tests/fixtures/n8n/job-feedback-event.json` as synthetic smoke input. Because imported workflows remain inactive, an operator must explicitly activate this workflow in the local n8n UI for the optional webhook smoke check, and deactivate it again afterward. Activation is permitted only for this loopback-only validation service; never activate or expose it on a public host. The callback accepts only schema version 1, the actions `applied`, `dismissed`, or `interesting`, bounded identity fields, and an unexpired token. Missing identity, malformed or expired tokens, replayed tokens, unknown or duplicate actions, oversized fields, and private/CV/profile/prompt/credential markers are rejected. Replay state is local workflow static data: each token-only replay key is retained until the token's declared expiry, with stale expired-entry cleanup and a hard 1024-entry cap; it is cleared with the disposable validation volume.

The n8n editor's Test/Manual execution is not a substitute for this webhook smoke check: it may not exercise the webhook trigger, and n8n does not persist workflow static data from test/manual executions as production-triggered executions do. Therefore replay behavior and bounded static-data state must be checked only through the activated local webhook, and may not be inferred from an editor run. This callback is not a production service, scheduler, notification channel, scoring input, or completion signal. Do not paste private payloads or credentials into n8n. Remove the imported workflow and validation data after the optional smoke check.

## Cleanup

Remove the disposable validation volume with the validation Compose file only:

```sh
docker compose --profile n8n-validation -f validation/n8n/docker-compose.yml down -v
```

This cleanup command is limited to this validation fixture; it must not target production Compose files or data.

## Mandatory static verification

The required hermetic check reads repository-controlled files only and does not start Docker, n8n, or any network service:

```sh
python -m pytest tests/test_n8n_compose_validation.py -v
```
