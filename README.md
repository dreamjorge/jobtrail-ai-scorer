# JobTrail AI Scorer

Provider-agnostic CLI that evaluates JobTrail jobs against a local candidate profile.

## Setup

```sh
python -m pip install .
cp scripts/scorer-config.example.yaml config.yaml
cp candidate-profile.example.md candidate-profile.md
# edit config.yaml (set candidate_profile_path: ./candidate-profile.md) and candidate-profile.md
# optionally set candidate_cv_path: /absolute/path/to/your/candidate-cv.md
```

The repo-root `config.example.yaml` is now a thin pointer to the canonical
example at `scripts/scorer-config.example.yaml`. Copy that canonical file
to a local, ignored `config.yaml` so there is exactly one source of truth for
the scorer configuration example. The leak-detector test
(`tests/test_example_redaction.py`) scans every committed example and template
for private IPv4 ranges, runtime paths, and credential prefixes and fails CI
on any regression. See [Runtime automation](docs/runtime-automation.md) for
the strict opt-in purge helper (`scripts/_purge_runtime_example.py --yes`)
that removes the historical runtime duplicate once your local runtime has
switched to the canonical file.

Run `jobtrail-ai-scorer score --config config.yaml [OPTIONS]`. Options include `--limit N`,
`--job-id ID`, `--dry-run`, `--force`, `--marker TEXT`, and `--provider hermes|openai_compatible`.
The default marker enables deduplication: jobs with a marked score are skipped; `--force` re-scores them.
`--dry-run` validates and reports scores without writing notes. Exit status is nonzero if any job fails.

For Hermes, configure `hermes_executable`, `hermes_profile`, and optional
`provider_timeout_seconds` in your local YAML (the executable must already be installed).
OpenAI-compatible providers use an endpoint/model and an API-key environment variable.

The optional `candidate_cv_path` points to a private, local CV file (for example,
`/absolute/path/to/your/candidate-cv.md`). When configured, its contents are included
alongside the candidate profile in the provider prompt. Keep the CV outside version control.

Prompt context is bounded before it is sent to the provider. The profile defaults to a
12,000-character budget and the CV defaults to 16,000 characters. Override these values
for a run with the positive integer environment variables `PROMPT_PROFILE_BUDGET` and
`PROMPT_CV_BUDGET`. Oversized sections receive a clear marker; customize it with
`PROMPT_TRUNCATE_MARKER` (default: `\n[... content truncated ...]`). A missing, directory,
or unreadable profile/CV logs a warning and the scorer continues with any remaining context.

Every run emits a single `prompt_tokens_estimate={...}` line with a per-section token
breakdown (`profile`, `cv`, `job`, `schema`, `instructions`, `total`) using a deterministic
approximation (`chars/4` by default, `words` as an opt-in alternative). Override the
estimator with `PROMPT_TOKEN_ESTIMATOR=chars4|words`. Set `PROMPT_TOKEN_BUDGET` to a
positive integer to enable a budget check; when the estimate exceeds the budget the scorer
prints a `prompt_token_budget={"budget": N, "total": M}` warning line in the same run.
The estimator never calls a real token counter or external API.

The CLI never stores API keys in configuration. Provider credentials are read from environment variables.
Never commit `config.yaml`, candidate profiles, CVs, credentials, or other secrets.
For Compose, override the example mounts with `SCORER_CONFIG_PATH` and
`SCORER_PROFILE_PATH` when running against your local files.

## Runtime automation

See [Runtime automation](docs/runtime-automation.md) for safe dry-run-first scheduler
setup, runtime install retention (`scripts/runtime_install.py`) and explicit backup
cleanup (`scripts/runtime_clean.py`), `SCORER_COMMAND` local launcher overrides,
Hermes Docker wrapper usage, optional WhatsApp notification through Hermes, dynamic
backend URL discovery
(precedence: published host port → Docker container IP → fail closed), Docker
maintenance rules, and the strict opt-in purge helper that removes the
historical runtime duplicate of the config example.

The canonical real-path flow lives at the root of `scripts/`
(`automated-job-search.example.py`, `hermes-docker-wrapper.example.sh`,
`notify-whatsapp-via-hermes.example.sh`) plus the strict opt-in
`_purge_runtime_example.py` helper. The historical dry-run wrappers
(`run-scorer.example.sh`, `hermes-score-jobs.sh`) are grouped under
[`scripts/legacy/`](scripts/legacy/README.md) with a `DEPRECATED` header and a
target removal date; do not use them in production. See the
[legacy README](scripts/legacy/README.md) for the replacement mapping.

## Automated JobTrail search

Run `scripts/automated-job-search.example.py` with required `SCORER_CONFIG_PATH`.
Configure `JOB_SEARCH_*`, `JOB_SCORE_THRESHOLD`, `SCORER_COMMAND`, and `WHATSAPP_NOTIFY_*`;
`SCORER_COMMAND` must be the direct `jobtrail-ai-scorer` CLI/launcher (not `run-scorer.sh`),
and notifications are disabled by default.

The launcher consults a seen cache before every `POST /api/discover/import` so
offers already imported within the last `2 * JOB_SEARCH_HOURS_OLD` hours are
skipped. The cache defaults to `<runtime-root>/jobtrail/logs/automated-job-search/seen.json`,
is rewritten atomically (`tmp + rename`), and is always `0600`. Override the
path with `JOBTRAIL_SEEN_CACHE_PATH`. Pass `--reset-seen-cache` (or set
`JOBTRAIL_RESET_SEEN_CACHE=1`) to clear the cache and force a re-import.
Corruption, missing parent directories, or permission errors degrade to an
empty cache and never crash the run.

Idempotent network calls (search, import, scorer subprocess, JobTrail reads)
are wrapped in a bounded retry helper. HTTP `5xx` and transient transport
errors are retried with exponential backoff (default 3 attempts, 0.5–8s
capped); HTTP `4xx` and configuration errors are terminal and recorded
without retries. Failures carry `retryable`, `exhausted`, or `terminal`
classifications on `AutomationRun.failures` so operators can distinguish
transient blips from persistent failures. Each retry attempt is logged with
the structured prefix `retry:`. Set `WHATSAPP_NOTIFY_ON_FAILURE=1` (default
off) to append a bounded failure summary to the WhatsApp helper message when
the run finishes with at least one failure.

Warning: enabled runs write AI score notes and may send one summary through WhatsApp, but never apply to jobs.
Every best-match summary exposes eleven allowlisted fields (title, company, location, score, recommendation,
recommendation label, strengths, gaps, external job URL, JobTrail link, and run identifier) and never embeds
descriptions, profiles, CVs, raw prompts, reasoning, notes, credentials, or secrets. The JobTrail link is
built from `JOBTRAIL_BASE_URL` plus `/jobs/<id>` and can be optionally rewritten through `WHATSAPP_SHORT_URL_BASE`.
See [Runtime automation](docs/runtime-automation.md) for the full field contract and the optional shortener.

## Hermetic end-to-end tests

The repository ships an in-process end-to-end suite at
`tests/test_automation_e2e.py` that exercises the full `search → import →
score → notify` pipeline through the real `JobTrailAutomation.run`
orchestration code. No Docker, systemd, or real HTTP is required:
`tests/stubs/` provides an in-process `http.server` JobTrail backend,
a fake scorer provider, and a WhatsApp buffer that captures the
rendered message. Run it with:

```sh
python -m pytest tests/test_automation_e2e.py -v
# or via the registered marker:
python -m pytest -m e2e -v
```

The suite asserts the five invariant contracts required by Issue #11
(happy path, dedup-skip-second-search, partial failure, redaction,
single notification) plus a triangulation test that verifies the
`GET /api/jobs/<id>` read-back path. See
[Runtime automation](docs/runtime-automation.md#hermetic-end-to-end-tests)
for the full contract.

## Runtime policy guardrail

The runtime `SOUL.md` and `skills/jobtrail-automation/SKILL.md` are Hermes profile files that live outside this repository. To enforce the apply-gate contract in CI, the repository ships minimal fixture mocks under `tests/fixtures/hermes/`. The guardrail (`tests/test_runtime_policy.py`, run by `.github/workflows/policy.yml` on every PR and daily at 06:00 UTC) asserts:

- the `SOUL.md` apply context contains the literal phrase `explicit confirmation`;
- the `SKILL.md` contains the literal phrase `explicit confirmation`;
- both files include `never submit` or `without an explicit confirmation` in the apply section.

Drift in the fixtures fails the workflow with a focused diff. The fixtures must remain pure contract mocks — never copy runtime paths (`/DATA/...`, `/AppData/...`), profile, CV, or credential content into the repository. See [Runtime automation](docs/runtime-automation.md#hermes-runtime-policy-guardrail-ci) for details and local-run instructions.
