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

### Funnel metrics

Run `jobtrail-ai-scorer metrics --period today|7d|30d --json` for a bounded,
privacy-safe funnel view. `today` is the current UTC day; `7d` and `30d` are
half-open rolling windows. Use `--journal-path`, `--seen-cache-path`, and
`--base-url` to override inputs. Set `JOBTRAIL_RUN_JOURNAL_PATH` to enable
automation run journaling (empty by default for compatibility). Missing
backend, journal, or score/application data is reported as `missing_data`;
application status is limited to fields exposed by the JobTrail API.

## Automated JobTrail search

Run `scripts/automated-job-search.example.py` with required `SCORER_CONFIG_PATH`.
Configure `JOB_SEARCH_*`, `JOB_SCORE_THRESHOLD`, `SCORER_COMMAND`, and `WHATSAPP_NOTIFY_*`;
`SCORER_COMMAND` must be the direct `jobtrail-ai-scorer` CLI/launcher (not `run-scorer.sh`),
and notifications are disabled by default. Optional `JOB_SEARCH_PROFILES` is a JSON array of
public search profile objects using only `name`, `search_terms`, `sites`, `locations`,
`results_wanted`, and `hours_old`; omit it to keep the legacy `JOB_SEARCH_*` fallback.
Do not place secrets, private local paths, CV/profile content, credentials, or new source
provider definitions in `JOB_SEARCH_PROFILES`.

The launcher consults a seen cache before every `POST /api/discover/import` so
offers already imported within the last `2 * JOB_SEARCH_HOURS_OLD` hours are
skipped. The cache defaults to `<runtime-root>/jobtrail/logs/automated-job-search/seen.json`,
is rewritten atomically (`tmp + rename`), and is always `0600`. Override the
path with `JOBTRAIL_SEEN_CACHE_PATH`. Pass `--reset-seen-cache` (or set
`JOBTRAIL_RESET_SEEN_CACHE=1`) to clear the cache and force a re-import.
Corruption, missing parent directories, or permission errors degrade to an
empty cache and never crash the run.

Idempotent network calls (search, scorer subprocess, JobTrail reads)
are wrapped in a bounded retry helper. HTTP `5xx` and transient transport
errors are retried with exponential backoff (default 3 attempts, 0.5–8s
capped). The non-idempotent `POST /api/discover/import` and note POSTs are
attempted only once; no import retry opt-in is provided without a verified
idempotency-key contract. HTTP `4xx` and configuration errors are terminal and recorded
without retries. Failures carry `retryable`, `exhausted`, or `terminal`
classifications on `AutomationRun.failures` so operators can distinguish
transient blips from persistent failures. Each retry attempt is logged with
the structured prefix `retry:`. Set `WHATSAPP_NOTIFY_ON_FAILURE=1` (default
off) to append a bounded failure summary to the WhatsApp helper message when
the run finishes with at least one failure.

Warning: enabled runs write AI score notes and may send one summary through WhatsApp, but never apply to jobs.
Every best-match summary exposes eleven required allowlisted fields (title, company, location, score, recommendation,
recommendation label, strengths, gaps, external job URL, JobTrail link, and run identifier) plus optional selected
public `searchProfiles` names when profiles contributed the selected job; it never embeds
descriptions, profiles, CVs, raw prompts, reasoning, notes, credentials, or secrets. The JobTrail link is
built from `JOBTRAIL_BASE_URL` plus `/jobs/<id>` and can be optionally rewritten through `WHATSAPP_SHORT_URL_BASE`.
See [Runtime automation](docs/runtime-automation.md) for the full field contract and the optional shortener.

### Optional Adzuna source

`scripts/automated-job-search.example.py` can route a `SourceSearchRequest`
to the optional Adzuna source when `adzuna` is included in `JOB_SEARCH_SITES`
(or in a per-profile `sites` list). The adapter reads its configuration
from the process environment so credentials never live in YAML or version
control:

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `ADZUNA_APP_ID` | yes (when enabled) | – | Adzuna application id. Must be set together with `ADZUNA_APP_KEY`. |
| `ADZUNA_APP_KEY` | yes (when enabled) | – | Adzuna application key. Must be set together with `ADZUNA_APP_ID`. |
| `ADZUNA_COUNTRY` | no | `us` | ISO 3166-1 alpha-2 lower-case country code (for example `mx`, `gb`, `de`). Interpolated into the Adzuna URL path and used to derive `salary_currency` on the normalized job. |
| `ADZUNA_BASE_URL` | no | `https://api.adzuna.com/v1` | Override the API base URL for staging or mirrored deployments. |
| `JOB_DISABLE_ADZUNA` | no | unset | Force-disable the source regardless of credentials. Accepts `1`, `true`, `yes`, `on`. |

`AdzunaConfig.from_env` is the single entry point: production callers pass
the resulting config to `AdzunaSourceAdapter`. The launcher never adds the
adapter when the config is `None`, so missing credentials fall back to the
remaining source adapters (for example JobSpy) without code changes.

Disable behavior:

- **Both credentials missing** — `AdzunaConfig.from_env` returns `None` and the source is silently disabled. No log line is emitted, so fresh installs never see noise.
- **Exactly one of `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` set** — `AdzunaConfig.from_env` returns `None` and emits a single `WARNING` line on the `jobtrail_ai_scorer.sources.adzuna` logger that names the exception class (`ValueError`) only. The credential value (or any substring of it) never appears in the warning.
- **`JOB_DISABLE_ADZUNA` truthy** — `AdzunaConfig.from_env` returns `None` regardless of credentials. Useful when an operator wants to opt out without removing the variables.

Secret hygiene is enforced end to end:

- The partial-credential warning never contains the credential value or any substring of it.
- HTTP errors (`AdzunaHttpError`, `AdzunaTransientError`) carry the status code and the exception class name only. The credentialed request URL (which contains `app_id=` and `app_key=` query parameters) is never embedded in the message, the failure label, or any retry metadata.
- `AutomationRun.failures` records failures as `search:<classification>:<ExceptionType>` so operators can grep for `search:terminal:AdzunaHttpError` without seeing credentials. A misconfigured or rejected Adzuna call never blocks the remaining adapters.

The adapter issues exactly one bounded GET per search request
(`{base_url}/jobs/{country}/search/1` with `results_per_page` capped at
`MAX_PER_PAGE` = 50). Pagination beyond that single page is intentionally
out of scope in this release and is not opted in by any environment
variable. See
[Optional Adzuna source](docs/runtime-automation.md#optional-adzuna-source)
in [Runtime automation](docs/runtime-automation.md) for the full contract
including the bounded-page boundary, the retry/error classification, and
how the orchestrator handles a failed Adzuna call without blocking JobSpy.

### Optional Lever source (PR-B)

`scripts/automated-job-search.example.py` can route a `SourceSearchRequest`
to the optional Lever source by adding Lever board slugs to
`JOB_ATS_BOARDS.lever_boards`. Unlike Adzuna, the Lever public postings
endpoint does not require authentication:

```json
{"lever_boards": ["acme", "globex"], "results_wanted": 25}
```

The adapter issues one bounded GET per configured board against the
public Lever endpoint:

```text
GET https://api.lever.co/v0/postings/<board>?mode=json
```

`request.search_term` and `request.location` are intentionally ignored —
the Lever endpoint scopes results to a single board slug and does not
accept free-text queries. `request.profile_name` is propagated to every
normalized job and `request.results_wanted` is the global cap across all
boards (so a configured `results_wanted=10` never returns more than 10
postings regardless of how many boards are configured).

The public endpoint exposes a single, bounded JSON array per board.
Multi-page or per-term filtering is intentionally out of scope; the
adapter never paginates and never follows pagination links. When the
board has no postings (or returns a non-list payload) the adapter treats
the call as a partial success and returns an empty list for that board.

Field translation (per the design table):

| Lever field | `NormalizedJob` field |
| --- | --- |
| `id` | `source_job_id` |
| `text` | `title` |
| `description` (HTML stripped) | `description` |
| `applyUrl` | `source_url` |
| `categories.location` + `categories.commitment` | `location` |
| board slug | `company` |
| request `profile_name` | `search_profile` |
| adapter clock (UTC ISO-8601) | `retrieved_at` |

Error semantics:

- `4xx` responses raise `LeverHttpError` immediately (terminal, no retry).
  The exception carries `status_code` and `board` so the orchestrator can
  classify the failure as `search:terminal:LeverHttpError`.
- `5xx` responses and transport errors are retried via the existing
  `RetryPolicy` (default `max_attempts=3`). Exhausted retries surface as
  `LeverTransientError` and are recorded as
  `search:terminal:LeverTransientError` (the orchestrator's classifier
  treats the exhausted wrapper as terminal and records the exception
  class name only).
- Malformed items inside the payload are silently skipped; only a full
  board failure raises.

Disable semantics: the source is enabled whenever
`JOB_ATS_BOARDS.lever_boards` is a non-empty list. Omit the key (or pass
an empty list) to disable Lever without removing the boards from the
configuration. A failing Lever adapter never blocks the remaining
adapters (for example JobSpy); see
[Optional Lever source](docs/runtime-automation.md#optional-lever-source)
in [Runtime automation](docs/runtime-automation.md) for the full
contract including the orchestrator integration.

### Optional Greenhouse source

Add Greenhouse board slugs under `JOB_ATS_BOARDS.greenhouse_boards`:

```json
{"greenhouse_boards": ["acme", "globex"], "results_wanted": 25}
```

The adapter makes one request per board to
`GET https://boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true`.
Search terms and locations are ignored; automation issues one ATS request
per configured search profile (not once per location), and uses the ATS
`results_wanted` cap across boards. Results are globally capped, normalized
with HTML-stripped content, board company, profile name, and a
UTC retrieval timestamp. Malformed rows are skipped. `4xx` errors are
terminal; `5xx` and transport failures use bounded retries and do not block
JobSpy or Lever results.

### Preflight checks and circuit breaker (Issue #37)

The daily automation can short-circuit an unhealthy run before it invests in
search/import/score work. Two opt-in features wire the gate:

- **Preflight checks** — a small set of bounded, side-effect-free probes
  (`jobtrail_api`, `jobspy_search`, `hermes_provider`, plus `whatsapp`
  when `WHATSAPP_NOTIFY_ENABLED=1`). When any *required* check reports
  `unavailable`, the run returns an empty `AutomationRun` with a single
  `preflight:unavailable:<name>` failure label per unavailable check.
- **Persistent circuit breaker** — a JSON-backed state machine that opens
  after `BREAKER_FAILURE_THRESHOLD` consecutive failed runs, blocks the
  pipeline for `BREAKER_COOLDOWN_SECONDS`, and emits at most one bounded
  WhatsApp alert per `BREAKER_ALERT_COOLDOWN_SECONDS`. A breaker-opened
  run returns an empty `AutomationRun` with the `breaker:open` failure
  label; the run never increments the consecutive-failure counter so the
  alert does not extend the cooldown.

The breaker is **opt-in**: omitting `BREAKER_STATE_PATH` (or leaving it
empty) keeps the legacy pipeline unchanged. Setting it to an absolute path
wires the breaker for that path; the file is rewritten atomically with
`0600` permissions (mirrors the seen-cache contract).

### Environment variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `BREAKER_FAILURE_THRESHOLD` | no | `3` | Consecutive failed runs that open the breaker. Must parse as an integer; non-integer values raise `ValueError` from `AutomationConfig.from_env`. |
| `BREAKER_COOLDOWN_SECONDS` | no | `3600.0` | Cooldown window during which an open breaker blocks the pipeline. Must parse as a float. |
| `BREAKER_ALERT_COOLDOWN_SECONDS` | no | `3600.0` | Minimum seconds between consecutive breaker-open WhatsApp alerts. Must parse as a float. |
| `BREAKER_STATE_PATH` | no | `""` (no breaker) | Absolute path to the JSON state file. Empty disables the breaker entirely so existing callers see no behavior change. When set, the file is created on first persistence with `0600` permissions. |

### Breaker state machine

```text
CLOSED ── consecutive_failures >= threshold ──▶ OPEN (opened_at = now)
                                                  │
                                          cooldown_seconds elapsed
                                                  │
                                                  ▼
                                             HALF_OPEN (attempts allowed)
                                             │       │
                                        success   failure
                                             │       │
                                             ▼       ▼
                                          CLOSED   OPEN
                                        (counter  (opened_at = now;
                                         reset;   no auto-alert until
                                         alert    alert_cooldown
                                         timer    elapses)
                                         reset)
```

- `should_attempt()` is `True` in `CLOSED`, and in `HALF_OPEN` (cooldown
  elapsed since `opened_at`); `False` otherwise.
- `record_failure()` increments the counter, opens the breaker at the
  threshold, and updates `opened_at` to "now" each time the breaker is
  (re)opened. It never fires an alert on its own.
- `record_success()` resets the breaker to `CLOSED` (`consecutive_failures=0`,
  `opened_at=None`) and resets the alert timer so the recovery alert
  can fire once.
- `try_alert()` returns `True` iff no alert has been emitted within
  `alert_cooldown_seconds`; on `True` it stamps `last_alert_at`.

### Preflight checks table

| Check | Probe | Required | Timeout | Notes |
| --- | --- | --- | --- | --- |
| `jobtrail_api` | `GET <base_url>/api/health` (falls back to `HEAD` on 404) | yes | 2.0s | Healthy when the response is 2xx (or the head probe succeeds). |
| `jobspy_search` | minimal `JobSpySourceAdapter.search` request | yes | 5.0s | Uses `RetryPolicy(max_attempts=1)`. |
| `hermes_provider` | `hermes --profile <profile> --help` (returncode `== 0` ⇒ healthy) | yes | 2.0s | Defaults `hermes_executable=hermes`, `hermes_profile=default`. |
| `whatsapp` | `os.access(<whatsapp_command>, os.X_OK)` | no (only added when `notify_enabled=True`) | 1.0s | Optional — a missing helper does not abort the run. |

A corrupt or missing breaker state file degrades to a fresh `CLOSED`
breaker so storage failures never crash the automation. A breaker-opened
run does not increment the failure counter and does not double-notify
within the alert cooldown. See
[Runtime automation](docs/runtime-automation.md#preflight-checks-and-circuit-breaker)
for the full contract and the test invariants.

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
