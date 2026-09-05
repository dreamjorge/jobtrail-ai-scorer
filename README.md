# JobTrail AI Scorer

Provider-agnostic CLI that evaluates JobTrail jobs against a local candidate profile.

## Setup

```sh
python -m pip install .
cp config.example.yaml config.yaml
# edit config.yaml and candidate-profile.md
```

Run `jobtrail-ai-scorer score --config config.yaml [OPTIONS]`. Options include `--limit N`,
`--job-id ID`, `--dry-run`, `--force`, `--marker TEXT`, and `--provider hermes|openai_compatible`.
The default marker enables deduplication: jobs with a marked score are skipped; `--force` re-scores them.
`--dry-run` validates and reports scores without writing notes. Exit status is nonzero if any job fails.

For Hermes, configure `hermes_executable`, `hermes_profile`, and optional
`provider_timeout_seconds` in your local YAML (the executable must already be installed).
OpenAI-compatible providers use an endpoint/model and an API-key environment variable.

The CLI never stores API keys in configuration. Provider credentials are read from environment variables.
Never commit `config.yaml`, candidate profiles, credentials, or other secrets.
For Compose, override the example mounts with `SCORER_CONFIG_PATH` and
`SCORER_PROFILE_PATH` when running against your local files.
