# Runtime Automation

## Automated search and scoring

Run `scripts/automated-job-search.example.py` from an operator-controlled scheduler. It searches LinkedIn and Indeed for the combined Python/C++/MATLAB/backend/API/database/automation/CI/CD/Docker/LLM/agent profile in Queretaro and globally remote roles, imports new results, and scores at most 10 imported jobs. `SCORER_CONFIG_PATH` is required.

Defaults are safe and bounded: `JOBTRAIL_BASE_URL=http://127.0.0.1:8000`, `JOB_SEARCH_RESULTS_WANTED=10`, `JOB_SEARCH_HOURS_OLD=72`, `JOB_SEARCH_MAX_SCORE=10`, and `JOB_SCORE_THRESHOLD=80`. Override `JOB_SEARCH_SITES`, `JOB_SEARCH_TERMS`, `JOB_SEARCH_LOCATIONS` (semicolon-separated), `SCORER_COMMAND`, and `WHATSAPP_NOTIFY_COMMAND` as needed.

Set `WHATSAPP_NOTIFY_COMMAND=./notify-whatsapp-via-hermes.local.sh` (the helper accepts the summary on stdin), then set `WHATSAPP_NOTIFY_ENABLED=1` only when the configured Hermes notification helper is ready. At most one summary is sent per run, and only for the highest validated score at or above the threshold. The summary contains title, company, location, score, recommendation, strengths, gaps, and URL only. This workflow writes `[AI_JOB_SCORE_V1]` notes and sends WhatsApp when enabled, but **never applies to jobs automatically**. It must not expose descriptions, profiles, prompts, notes, credentials, or secrets.

Use these examples to run JobTrail AI Scorer from a local scheduler while keeping private runtime files out of the repository. Copy the example files, edit only local ignored copies, and dry-run first before allowing writes to JobTrail notes.

## Backend URL resolution

The systemd service must reach the JobTrail backend. Instead of baking a private
host or Docker IP into `JOBTRAIL_BASE_URL`, the launcher resolves the URL on every
start through `jobtrail_ai_scorer.discover.resolve_backend_url`. The precedence
is fixed and lives in one place (`src/jobtrail_ai_scorer/discover.py`):

1. **`http://127.0.0.1:8000`** (the published host port). The probe issues a
   short-timeout HTTP GET via `urllib.request`; any reply (including 404 on the
   root path) is treated as "reachable" because the backend may not expose a
   health route.
2. **Docker container IP** resolved by
   `docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' <container>`
   pointed at the container name from `--container` (default
   `jobtrail-backend-1`) or `JOBTRAIL_DISCOVER_CONTAINER`. The probe of the
   resulting URL must succeed.
3. **Fail closed**: the resolver raises `BackendDiscoveryError` with a clear
   message naming the published URL and the container. The launcher exits with
   status `2`, so the systemd unit will mark the run as failed instead of
   silently pointing at a stale address.

The precedence is never baked into runtime environment files; the env stores
only the container name and overrides, never an IP. The example YAML config
never contains a private IP.

### Launcher flags

`scripts/automated-job-search.example.py` exposes:

- `--container NAME`: Docker container to inspect when the published port is
  unreachable. Default: `jobtrail-backend-1`.
- `--base-url URL`: bypass discovery and use the URL verbatim (highest priority).
- `--no-discover`: skip the published-port and Docker probes; use
  `JOBTRAIL_BASE_URL` only.

The launcher prints the chosen `base_url` and `source` (`published-port`,
`docker-container`, `static`, or `cli`) to stderr so systemd logs show which
branch served the run. Discovery errors are surfaced on stderr with the original
`BackendDiscoveryError` message.

### Disabling discovery

Existing systemd units that already export `JOBTRAIL_BASE_URL=http://<host>:8000`
do not need to change. Either:

- leave `JOBTRAIL_DISCOVER_CONTAINER` unset (the launcher falls back to
  `JOBTRAIL_BASE_URL`/`--base-url`); or
- pass `--no-discover` to make the static-URL path explicit and traceable in
  the unit definition.


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
export SCORER_COMMAND=jobtrail-ai-scorer  # direct CLI/launcher only; never run-scorer.sh
export WHATSAPP_NOTIFY_COMMAND=./notify-whatsapp-via-hermes.local.sh  # reads summary from stdin
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

Use `SCORER_COMMAND` when the direct scorer CLI/launcher is not on `PATH`; it defaults to `jobtrail-ai-scorer`. It is invoked as `SCORER_COMMAND score --config CONFIG --job-id ID`; do not set it to the batch `run-scorer.sh` runner. Use a small `SCORER_LIMIT` until scheduling behavior is proven. The runner preserves the scorer exit code and prints the log path.

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
