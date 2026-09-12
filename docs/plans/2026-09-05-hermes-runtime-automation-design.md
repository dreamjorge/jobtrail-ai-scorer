# Hermes Runtime Automation Design

> **Superseded notification contract:** the current launcher renders bounded cards and uses Hermes direct `send --to "$HERMES_WHATSAPP_TARGET"`; this earlier design's LLM-mediated formatting is retained only as historical context.

## Goal

Provide a safe local runner for JobTrail AI scoring on the Orange Pi runtime, with optional WhatsApp notification routed through the existing Hermes `job-search` profile.

## Scope

This design covers repository-tracked examples, scripts, and documentation that make the runtime workflow repeatable without committing private paths, profiles, tokens, candidate data, or deployment-specific config. It does not send WhatsApp messages during tests, does not integrate Twilio/Meta directly, and does not manage JobTrail Docker lifecycle beyond documented safe usage.

## Architecture

Keep the scorer as the core boundary: it reads JobTrail jobs, calls a provider, validates `ScoreResult`, and writes notes only when not in dry-run mode. Runtime automation sits around the scorer through shell scripts and ignored local config.

The local deployment uses a small Hermes wrapper executable that forwards provider calls into the existing `hermes` Docker container. WhatsApp notification is optional and also routed through Hermes using the same `job-search` profile, so the Python package does not need WhatsApp credentials or direct messaging dependencies.

## Components

- `scripts/hermes-docker-wrapper.example.sh`: example executable wrapper for `docker exec hermes /opt/hermes/.venv/bin/hermes "$@"`.
- `scripts/run-scorer.example.sh`: example runner that accepts local config paths, supports dry-run first, and captures a concise run summary.
- `scripts/notify-whatsapp-via-hermes.example.sh`: optional notification helper that sends an already-rendered bounded run summary through Hermes direct `send --to "$HERMES_WHATSAPP_TARGET"` when explicitly invoked.
- `docs/runtime-automation.md`: documents Orange Pi-safe compose usage, ignored local config, dry-run-first workflow, scheduler options, and WhatsApp/Hermes assumptions.

## Data flow

1. Scheduler invokes the runner with a local config path.
2. Runner calls `jobtrail-ai-scorer score --config <config> --limit <N>`; initial setup uses `--dry-run`.
3. Scorer calls JobTrail HTTP API and Hermes through the configured wrapper.
4. Runner records stdout/stderr to logs and preserves the exit code.
5. Optional notification helper sends only the summary and exit code through Hermes/WhatsApp, not full job descriptions or candidate profile content.

## Error handling

The runner must not run destructive Docker commands. It should fail fast if config files are missing, preserve scorer exit codes, and keep logs outside the repository by default. WhatsApp notification failure must not hide scorer failure; the notification helper reports its own failure separately.

## Testing

Use TDD for code or script behavior added to the repository. Tests should verify command construction and safety constraints without invoking Docker, Hermes, WhatsApp, or private deployment paths. Runtime smoke checks are manual and read-only unless explicitly authorized.

## Security and privacy

Committed files are examples only. Local config, candidate profiles, logs, tokens, and private deployment paths stay ignored. Notifications must include only operational summaries, never raw prompts, job descriptions, notes, candidate profiles, or credentials.
