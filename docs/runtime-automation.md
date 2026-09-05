# Runtime Automation

Use these examples to run JobTrail AI Scorer from a local scheduler while keeping private runtime files out of the repository. Copy the example files, edit only local ignored copies, and dry-run first before allowing writes to JobTrail notes.

## Safety rules

- **Dry-run first:** keep `SCORER_DRY_RUN=1` until the config, JobTrail connection, provider, and logs look correct.
- Local config stays ignored. Do not commit `config.yaml`, candidate profiles, logs, secrets, tokens, or copied runtime scripts containing machine-specific paths.
- Store logs outside the repository, for example under `/tmp/jobtrail-ai-scorer-logs` or another local operator-owned directory.
- For JobTrail maintenance commands, use both JobTrail compose files together: `compose.hub.yml` and `compose.override.yml`. Keep maintenance commands explicit so the override services, ports, and mounts are included.
- Never run `docker compose down -v`; it can delete volumes and data. Prefer targeted `docker compose ... restart`, `logs`, or `exec` maintenance commands.
- Do not run Docker, Hermes, or WhatsApp automation from this document until you have reviewed the copied local files.

Example JobTrail maintenance command shape:

```sh
docker compose -f compose.hub.yml -f compose.override.yml ps
docker compose -f compose.hub.yml -f compose.override.yml logs --tail=100 jobtrail
```

## Copy the example files

```sh
cp config.example.yaml config.yaml
cp candidate-profile.example.md candidate-profile.md
cp scripts/hermes-docker-wrapper.example.sh ./hermes-docker-wrapper.local.sh
cp scripts/run-scorer.example.sh ./run-scorer.local.sh
cp scripts/notify-whatsapp-via-hermes.example.sh ./notify-whatsapp-via-hermes.local.sh
chmod +x ./hermes-docker-wrapper.local.sh ./run-scorer.local.sh ./notify-whatsapp-via-hermes.local.sh
```

Edit the copied files or environment variables for your host. Keep public/example values in committed examples; put real local paths, profile text, and credentials only in ignored local files or environment variables.

## Local config example

`config.yaml` should be a local ignored file. This example uses public placeholders only:

```yaml
jobtrail_base_url: http://localhost:3000
candidate_profile_path: ./candidate-profile.md
provider: hermes
hermes_executable: ./hermes-docker-wrapper.local.sh
hermes_profile: job-search
provider_timeout_seconds: 120
marker: "[AI_JOB_SCORE_V1]"
```

The CLI reads provider credentials from environment variables when a provider needs them. Do not place tokens or secrets in YAML.

## Hermes Docker wrapper script

When Hermes is running in Docker, point `hermes_executable` at the copied Hermes Docker wrapper script. The example `scripts/hermes-docker-wrapper.example.sh` runs:

```sh
docker exec "$HERMES_CONTAINER" "$HERMES_BIN" "$@"
```

Configure `HERMES_CONTAINER` and `HERMES_BIN` in the scheduler environment if your container name or Hermes path differs.

## Runner usage

Start with dry-run mode:

```sh
export SCORER_CONFIG_PATH="$PWD/config.yaml"
export SCORER_COMMAND=jobtrail-ai-scorer  # or /path/to/local/launcher
export SCORER_LIMIT=1
export SCORER_DRY_RUN=1
export SCORER_LOG_DIR=/tmp/jobtrail-ai-scorer-logs
./run-scorer.local.sh
```

After reviewing the dry-run log, run real mode only when you are ready for the scorer to write JobTrail notes:

```sh
export SCORER_DRY_RUN=0
./run-scorer.local.sh
```

Use `SCORER_COMMAND` when the scorer launcher is not on `PATH`; it defaults to `jobtrail-ai-scorer`. Use a small `SCORER_LIMIT` until scheduling behavior is proven. The runner preserves the scorer exit code and prints the log path.

## Scheduler choices

Choose one scheduler; do not enable duplicate schedules.

- `cron`: simple host-level scheduling. Export `SCORER_CONFIG_PATH`, `SCORER_COMMAND` when needed, `SCORER_LIMIT`, `SCORER_DRY_RUN`, and `SCORER_LOG_DIR` in the crontab entry or a sourced local environment file.
- `systemd timer`: useful when you want journal integration, explicit dependencies, and retry policy. Put private values in an environment file outside the repository.
- `OpenClaw cron`: use when the runtime is managed by OpenClaw and the job should live with that operational schedule. Mount only ignored local config/profile files and write logs to an operator-owned location.

Keep the first scheduled run in dry-run first mode, then switch to real mode after reviewing output.

## WhatsApp via Hermes `job-search`

WhatsApp notification is optional and disabled unless `WHATSAPP_NOTIFY_ENABLED=1`. The helper sends through Hermes with the `job-search` profile by default:

```sh
export WHATSAPP_NOTIFY_ENABLED=1
export HERMES_EXECUTABLE=./hermes-docker-wrapper.local.sh
export HERMES_PROFILE=job-search
./notify-whatsapp-via-hermes.local.sh "2 new high-confidence matches; log: /tmp/jobtrail-ai-scorer-logs/scorer-example.log"
```

Send summaries only. Do not include raw prompts, candidate profile content, job descriptions, notes, tokens, credentials, or secrets in WhatsApp messages. Include counts, status, and log metadata that an operator can use to inspect the local log securely.
