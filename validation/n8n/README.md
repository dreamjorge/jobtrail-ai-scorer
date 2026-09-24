# Local n8n validation

This is an optional, operator-only localhost smoke check for the bounded JobTrail-to-n8n handoff. It is not a production service, is not part of normal startup or systemd, and is explicitly non-CI.

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
