# JobTrail AI Scorer

Provider-agnostic CLI that evaluates JobTrail jobs against a local candidate profile.

## Setup

```sh
python -m pip install .
cp config.example.yaml config.yaml
cp candidate-profile.example.md candidate-profile.md
# edit config.yaml (set candidate_profile_path: ./candidate-profile.md) and candidate-profile.md
# optionally set candidate_cv_path: /DATA/AppData/jobtrail/candidate-cv.md
```

Run `jobtrail-ai-scorer score --config config.yaml [OPTIONS]`. Options include `--limit N`,
`--job-id ID`, `--dry-run`, `--force`, `--marker TEXT`, and `--provider hermes|openai_compatible`.
The default marker enables deduplication: jobs with a marked score are skipped; `--force` re-scores them.
`--dry-run` validates and reports scores without writing notes. Exit status is nonzero if any job fails.

For Hermes, configure `hermes_executable`, `hermes_profile`, and optional
`provider_timeout_seconds` in your local YAML (the executable must already be installed).
OpenAI-compatible providers use an endpoint/model and an API-key environment variable.

The optional `candidate_cv_path` points to a private, local CV file (for example,
`/DATA/AppData/jobtrail/candidate-cv.md`). When configured, its contents are included
alongside the candidate profile in the provider prompt. Keep the CV outside version control.

The CLI never stores API keys in configuration. Provider credentials are read from environment variables.
Never commit `config.yaml`, candidate profiles, CVs, credentials, or other secrets.
For Compose, override the example mounts with `SCORER_CONFIG_PATH` and
`SCORER_PROFILE_PATH` when running against your local files.

## Runtime automation

See [Runtime automation](docs/runtime-automation.md) for safe dry-run-first scheduler
setup, `SCORER_COMMAND` local launcher overrides, Hermes Docker wrapper usage,
optional WhatsApp notification through Hermes, dynamic backend URL discovery
(precedence: published host port → Docker container IP → fail closed), and
Docker maintenance rules.

## Automated JobTrail search

Run `scripts/automated-job-search.example.py` with required `SCORER_CONFIG_PATH`.
Configure `JOB_SEARCH_*`, `JOB_SCORE_THRESHOLD`, `SCORER_COMMAND`, and `WHATSAPP_NOTIFY_*`;
`SCORER_COMMAND` must be the direct `jobtrail-ai-scorer` CLI/launcher (not `run-scorer.sh`),
and notifications are disabled by default.

The launcher consults a seen cache before every `POST /api/discover/import` so
offers already imported within the last `2 * JOB_SEARCH_HOURS_OLD` hours are
skipped. The cache defaults to `/DATA/AppData/jobtrail/logs/automated-job-search/seen.json`,
is rewritten atomically (`tmp + rename`), and is always `0600`. Override the
path with `JOBTRAIL_SEEN_CACHE_PATH`. Pass `--reset-seen-cache` (or set
`JOBTRAIL_RESET_SEEN_CACHE=1`) to clear the cache and force a re-import.
Corruption, missing parent directories, or permission errors degrade to an
empty cache and never crash the run.

Warning: enabled runs write AI score notes and may send one summary through WhatsApp, but never apply to jobs.
Summaries exclude descriptions, profiles, prompts, notes, credentials, and secrets.
See [Runtime automation](docs/runtime-automation.md).
