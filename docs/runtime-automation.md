# Runtime Automation

## Automated search and scoring

Run `scripts/automated-job-search.example.py` from an operator-controlled scheduler. It searches LinkedIn and Indeed for the combined Python/C++/MATLAB/backend/API/database/automation/CI/CD/Docker/LLM/agent profile in Queretaro and globally remote roles, imports new results, and scores at most 10 imported jobs. `SCORER_CONFIG_PATH` is required.

Defaults are safe and bounded: `JOBTRAIL_BASE_URL=http://127.0.0.1:8000`, `JOB_SEARCH_RESULTS_WANTED=10`, `JOB_SEARCH_HOURS_OLD=72`, `JOB_SEARCH_MAX_SCORE=10`, and `JOB_SCORE_THRESHOLD=80`. Override `JOB_SEARCH_SITES`, `JOB_SEARCH_TERMS`, `JOB_SEARCH_LOCATIONS` (semicolon-separated), `SCORER_COMMAND`, and `WHATSAPP_NOTIFY_COMMAND` as needed. Optional `JOB_SEARCH_PROFILES` is a JSON array for multiple public search profiles; omit it to use the legacy single-search `JOB_SEARCH_*` fallback.

Set `WHATSAPP_NOTIFY_COMMAND=./notify-whatsapp-via-hermes.local.sh` (the helper accepts the summary on stdin), then set `WHATSAPP_NOTIFY_ENABLED=1` only when the configured Hermes notification helper is ready. At most one summary is sent per run, and only for the highest validated score at or above the threshold. The summary contains title, company, location, score, recommendation, recommendation label, strengths, gaps, the external job URL, the JobTrail link, and the run identifier, plus optional selected public `searchProfiles` names only when profile provenance exists. See [Daily WhatsApp summary fields](#daily-whatsapp-summary-fields) for the contract and the optional `WHATSAPP_SHORT_URL_BASE` shortener. This workflow writes `[AI_JOB_SCORE_V1]` notes and sends WhatsApp when enabled, but **never applies to jobs automatically**. It must not expose descriptions, profiles, prompts, notes, credentials, or secrets.

### Full-automation dry-run

Use `scripts/automated-job-search.example.py --dry-run`, or set
`JOBTRAIL_AUTOMATION_DRY_RUN=1`, to exercise the full search-and-score pipeline without
side effects. The CLI flag takes precedence over the environment setting. Output includes
`planned_operations` (`searched`, `imported`, `scored`, and `notified`) and a deterministic,
redacted `notification_preview`. In this mode searches and scorer previews run, but jobs are
not imported, score notes are not saved, WhatsApp is not called, and the seen cache, circuit
breaker, and run journal are not created or changed. This is distinct from the scorer-only
`jobtrail-ai-scorer score --dry-run`, which does not perform the full automation search.

Use these examples to run JobTrail AI Scorer from a local scheduler while keeping private runtime files out of the repository. Copy the example files, edit only local ignored copies, and dry-run first before allowing writes to JobTrail notes.

> **Deprecation note.** The historical dry-run wrappers
> `scripts/run-scorer.example.sh` and `scripts/hermes-score-jobs.sh` have been
> moved under [`scripts/legacy/`](scripts/legacy/README.md) with a
> `DEPRECATED` header. Do not use them in production; the canonical real-path
> flow is `scripts/automated-job-search.example.py`. See
> [`scripts/legacy/README.md`](scripts/legacy/README.md) for the replacement
> mapping and target removal date.

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

Leaving `--container` unset does **not** disable discovery: it falls back to
`JOBTRAIL_DISCOVER_CONTAINER` if set, else `jobtrail-backend-1`, and the
launcher still probes the published port and then that container name,
exiting `2` if neither is reachable.

To make an existing systemd unit that already exports
`JOBTRAIL_BASE_URL=http://<host>:8000` use that URL as-is, pass
`--no-discover` (or `--base-url`) explicitly in the unit definition — this is
the only way to skip the published-port and Docker probes.


## Search profiles

`JOB_SEARCH_PROFILES` may define several public search profiles in a JSON array. Allowed keys are exactly:

- `name` (required, non-blank, unique): public label used for per-profile counts and optional notification provenance.
- `search_terms`: terms for that profile; defaults to `JOB_SEARCH_TERMS` when omitted.
- `sites`: list of existing supported source site names; defaults to `JOB_SEARCH_SITES` when omitted.
- `locations`: list of locations; defaults to `JOB_SEARCH_LOCATIONS` when omitted.
- `results_wanted`: integer result limit; defaults to `JOB_SEARCH_RESULTS_WANTED` when omitted.
- `hours_old`: integer recency window; defaults to `JOB_SEARCH_HOURS_OLD` when omitted.

Example:

```json
[
  {"name": "python", "search_terms": "python backend", "sites": ["linkedin"], "locations": ["remote"], "results_wanted": 5},
  {"name": "automation", "search_terms": "ci cd automation"}
]
```

The JSON is configuration only: it must not contain secrets, credentials, private filesystem paths, CV/profile text, provider prompts, or new source-provider definitions. `sites` only selects among the source providers already supported by the automation boundary. If `JOB_SEARCH_PROFILES` is unset, the launcher preserves the legacy single-profile behavior using `JOB_SEARCH_SITES`, `JOB_SEARCH_TERMS`, and `JOB_SEARCH_LOCATIONS`.

The final launcher output includes `profile_counts` with `searched`, `imported`, `duplicates`, and `failures` per profile when profiles are configured; it is `{}` for the legacy fallback.

## Optional Adzuna source

`AdzunaSourceAdapter` is an optional source the automation launcher can
include by adding `adzuna` to `JOB_SEARCH_SITES` or to a per-profile
`sites` list. The adapter reads its configuration from the process
environment so credentials never live in YAML, fixture files, or version
control.

### Environment variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `ADZUNA_APP_ID` | yes (when enabled) | – | Adzuna application id. Must be set together with `ADZUNA_APP_KEY`. |
| `ADZUNA_APP_KEY` | yes (when enabled) | – | Adzuna application key. Must be set together with `ADZUNA_APP_ID`. |
| `ADZUNA_COUNTRY` | no | `us` | ISO 3166-1 alpha-2 lower-case country code (for example `mx`, `gb`, `de`). Interpolated into the Adzuna URL path and used to derive `salary_currency` on the normalized job. |
| `ADZUNA_BASE_URL` | no | `https://api.adzuna.com/v1` | Override the API base URL for staging or mirrored deployments. |
| `JOB_DISABLE_ADZUNA` | no | unset | Force-disable the source regardless of credentials. Accepts `1`, `true`, `yes`, `on`. |

`AdzunaConfig.from_env` is the single source of truth: production callers
pass the resulting config to `AdzunaSourceAdapter(config=...)`. When the
adapter is omitted (or returns `None`) the launcher falls back to the
remaining source adapters (for example `JobSpySourceAdapter`) without
code changes.

### Disable semantics

- **Both `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` missing** —
  `AdzunaConfig.from_env` returns `None` and the source is silently
  disabled. No log line is emitted, so fresh installs that never intend
  to use Adzuna see no noise.
- **Exactly one of `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` set** —
  `AdzunaConfig.from_env` returns `None` and emits a single `WARNING`
  line on the `jobtrail_ai_scorer.sources.adzuna` logger that names the
  exception class (`ValueError`) only. The credential value (or any
  substring of it) never appears in the warning, the failure label,
  retry metadata, or any notification.
- **`JOB_DISABLE_ADZUNA` truthy** — `AdzunaConfig.from_env` returns
  `None` regardless of credentials. Use this when an operator wants to
  opt out without removing the variables from the environment.

The silent path is the right default: a fresh install with no Adzuna
credentials still works, and only an incomplete configuration produces a
single diagnostic warning.

### Secrets are never logged

Secret hygiene is enforced end to end:

- The partial-credential warning names the exception class only. Neither
  `ADZUNA_APP_ID` nor `ADZUNA_APP_KEY` (or any substring of them) ever
  appears in the warning text, the captured log records, or the
  rendered failure label.
- HTTP errors raised by the adapter (`AdzunaHttpError` for `4xx`,
  `AdzunaTransientError` after retry exhaustion) carry the status code
  and the exception class name. The credentialed request URL (which
  contains `app_id=` and `app_key=` query parameters) is never embedded
  in the message, the failure label, or any retry metadata.
- `AutomationRun.failures` records the failure as
  `search:<classification>:<ExceptionType>` (for example
  `search:terminal:AdzunaHttpError` or `search:exhausted:AdzunaTransientError`)
  so operators can grep for the misconfiguration without seeing
  credentials.
- The orchestrator catches `AdzunaSourceAdapter` exceptions at the
  search stage and continues with the remaining source adapters (for
  example JobSpy), so a misconfigured or rejected Adzuna call never
  blocks the run.

### Bounded page, no pagination

`AdzunaSourceAdapter` issues exactly one bounded GET per search
request:

```text
GET {base_url}/jobs/{country}/search/1
    ?app_id={ADZUNA_APP_ID}
    &app_key={ADZUNA_APP_KEY}
    &what={search_term}
    &where={location}
    &results_per_page={min(results_wanted, MAX_PER_PAGE)}
    &max_days_old={hours_old}
```

`results_per_page` is capped at `MAX_PER_PAGE` (50), so the adapter
never paginates within a single call. Following `redirect_url` links
for detail pages is not part of this slice. Multi-page support is
intentionally out of scope and is not opted in by any environment
variable in this release.

### Retry and error classification

The adapter reuses the project's `RetryPolicy` and `retry_call` so
transient transport errors and `5xx` responses retry with bounded
attempts (default `max_attempts=3`, `base_delay=0.5s`, `max_delay=8s`).
`4xx` responses raise `AdzunaHttpError` immediately (terminal, no retry);
exhausted retries surface as `AdzunaTransientError` so the orchestrator
records a stable failure label without leaking the request URL. See
[Bounded retries with backoff](#bounded-retries-with-backoff) for the
retry helper contract and the `retry:`/`retryable`/`exhausted`/
`terminal` classification vocabulary.

## Optional Lever source

`LeverSourceAdapter` is an optional ATS source the automation launcher
includes whenever `JOB_ATS_BOARDS.lever_boards` is a non-empty list. The
public Lever postings endpoint does not require authentication, so the
adapter does not read any credential variables.

### Enabling the source

Set `JOB_ATS_BOARDS` to a JSON object with a `lever_boards` key holding
the board slugs you want to query:

```json
{"lever_boards": ["acme", "globex"], "results_wanted": 25}
```

Validation lives in `parse_ats_boards` (see
[Automated search and scoring](#automated-search-and-scoring)): the
parser rejects non-string, blank, or duplicate entries and bounds
`results_wanted` to the closed interval `[1, 200]` (default 25). When
`lever_boards` is empty (or `JOB_ATS_BOARDS` is unset) the factory
returns no Lever adapter and the launcher falls back to the remaining
source adapters (for example `JobSpySourceAdapter`) without code
changes.

### Public endpoint, single bounded page

`LeverSourceAdapter` issues exactly one bounded GET per configured
board:

```text
GET https://api.lever.co/v0/postings/<board>?mode=json
```

The public Lever endpoint scopes results to a single board slug and
does not accept free-text queries, so `request.search_term` and
`request.location` are intentionally ignored. `request.profile_name`
is propagated to every normalized job and `request.results_wanted`
is the global cap across all boards: a configured `results_wanted=10`
never returns more than 10 postings regardless of how many boards
are configured. The adapter never paginates within a single call and
does not follow detail-page links; multi-page support is intentionally
out of scope.

### Field translation

| Lever field | `NormalizedJob` field | Notes |
| --- | --- | --- |
| `id` | `source_job_id` | Coerced to `str` when present. |
| `text` | `title` | – |
| `description` | `description` | HTML stripped via `ats_common._strip_html` (stdlib `html.parser`). |
| `applyUrl` | `source_url` | – |
| `categories.location` + `categories.commitment` | `location` | Joined with ` / ` when both present; one only or `None` otherwise. |
| board slug (the URL segment) | `company` | – |
| request `profile_name` | `search_profile` | – |
| adapter clock (UTC ISO-8601 with `Z` suffix) | `retrieved_at` | Naive clocks are tagged as UTC; aware clocks are converted to UTC. |

Optional fields (`location`, `description`, `title`, `source_url`) are
omitted from the import payload when their source data is missing;
`source_job_id` is required (a posting without an `id` is silently
dropped from the batch).

### Retry and error classification

The adapter reuses the project's `RetryPolicy` and `retry_call`:

- `4xx` responses raise `LeverHttpError` immediately (terminal, no
  retry). The exception carries `status_code` and `board` so the
  orchestrator can classify the failure as
  `search:terminal:LeverHttpError`. The message names the status
  code only; the request URL (which contains the board slug) is not
  embedded in the failure label.
- `5xx` responses and transient transport errors retry with bounded
  attempts (default `max_attempts=3`, `base_delay=0.5s`, `max_delay=8s`).
  Exhausted retries surface as `LeverTransientError` so the
  orchestrator records a stable failure label without leaking the
  request URL.
- Malformed items inside the payload (non-mappings, postings without
  an `id`) are silently skipped; only a full board failure raises.
  An empty body, scalar payload, or non-list JSON is treated as a
  partial success and converted to an empty list.

### Orchestrator integration

`build_ats_adapters(ats_boards)` constructs the
`LeverSourceAdapter` whenever `ats_boards.lever_boards` is non-empty and
`GreenhouseSourceAdapter` whenever `ats_boards.greenhouse_boards` is non-empty.
The factory is the single entry point so
`JobTrailAutomation` can build its default `source_adapters` tuple
without importing each adapter module directly.

When the Lever call raises, the orchestrator catches the exception at
the `search:` stage and records it on `AutomationRun.failures` as
`search:<classification>:<ExceptionType>` (for example
`search:terminal:LeverHttpError` or
`search:terminal:LeverTransientError`). The remaining source adapters
(for example `JobSpySourceAdapter`) continue with their own search
calls, so a failing Lever adapter never blocks the run. The
classification wrapper never embeds credentials or the request URL.

## Preflight checks and circuit breaker

The daily automation gates itself with two opt-in features before
investing in search/import/score work:

- **Preflight checks** — a small set of bounded, side-effect-free probes
  built by `jobtrail_ai_scorer.preflight.default_preflight_checks`. Each
  probe runs through `concurrent.futures.Future` with an explicit
  per-check timeout so a wedged upstream can never block the
  orchestrator. Status mapping:

  - `healthy` — the probe returned successfully within its timeout.
  - `degraded` — the probe raised `httpx.HTTPStatusError` with a `4xx`
    code (the upstream is reachable but unhappy).
  - `unavailable` — the probe raised a `5xx` code, transport error
    (`TimeoutException` / `ConnectError` / `OSError`),
    `subprocess.TimeoutExpired` / `FileNotFoundError`, or exceeded the
    per-check timeout.

  `should_abort` is `True` iff any *required* check is `unavailable`.
  The orchestrator records one `preflight:unavailable:<name>` failure
  label per unavailable required check and returns an empty
  `AutomationRun` without touching the search/import/score pipeline.

- **Persistent circuit breaker** — a JSON-backed state machine in
  `jobtrail_ai_scorer.circuit_breaker`. The breaker's state file
  defaults to
  `/DATA/AppData/jobtrail/logs/automated-job-search/breaker.json`
  (overridable via `BREAKER_STATE_PATH`) and uses the same atomic-JSON
  contract as the seen cache. A corrupt or missing state file degrades
  to a fresh `CLOSED` breaker so storage failures never crash the
  automation.

### Preflight checks table

| Name | Probe | Required | Timeout | Notes |
| --- | --- | --- | --- | --- |
| `jobtrail_api` | `GET <base_url>/api/health` (falls back to `HEAD` on 404) | yes | 2.0s | Healthy when the response is `2xx` (or the head probe succeeds). |
| `jobspy_search` | minimal `JobSpySourceAdapter.search` request | yes | 5.0s | Uses `RetryPolicy(max_attempts=1)`. |
| `hermes_provider` | `hermes --profile <profile> --help` (returncode `== 0` ⇒ healthy) | yes | 2.0s | Defaults to `hermes_executable=hermes`, `hermes_profile=default`. |
| `whatsapp` | `os.access(<whatsapp_command>, os.X_OK)` | no (only added when `WHATSAPP_NOTIFY_ENABLED=1`) | 1.0s | Optional — a missing helper does not abort the run. |

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

- `should_attempt()` is `True` in `CLOSED`, and in `HALF_OPEN`
  (cooldown elapsed since `opened_at`); `False` otherwise.
- `record_failure()` increments the counter, opens the breaker at
  the threshold, and updates `opened_at` to "now" each time the
  breaker is (re)opened. It never fires an alert on its own.
- `record_success()` resets the breaker to `CLOSED`
  (`consecutive_failures=0`, `opened_at=None`) **and** resets the
  alert timer so the recovery alert can fire once.
- `try_alert()` returns `True` iff no alert has been emitted within
  `alert_cooldown_seconds`; on `True` it stamps `last_alert_at`.

### Operator-facing behavior

- **Healthy preflight + closed breaker:** the existing pipeline runs
  unchanged. A clean run closes the breaker (counter reset, alert
  timer reset) so the next `try_alert()` returns `True`.
- **Preflight unavailable:** empty `AutomationRun`, one
  `preflight:unavailable:<name>` failure label per unavailable
  required check, no WhatsApp message (the legacy failure summary
  is also suppressed because no best-match was selected).
- **Breaker open:** empty `AutomationRun`, single `breaker:open`
  failure label. If `breaker.try_alert()` returns `True` *and*
  `WHATSAPP_NOTIFY_ENABLED=1` (or `WHATSAPP_NOTIFY_ON_FAILURE=1`),
  one bounded `{"kind": "breaker_open", "message": "..."}` alert is
  sent through the configured WhatsApp helper. The breaker-opened run
  MUST NOT increment the failure counter (so the alert does not
  extend the cooldown) and MUST NOT re-alert within
  `BREAKER_ALERT_COOLDOWN_SECONDS`.
- **Recovery:** a successful run during `HALF_OPEN` closes the
  breaker. `record_success()` clears `last_alert_at` so the recovery
  alert can fire on the next `try_alert()`.

### Opt-in / opt-out

- Set `BREAKER_STATE_PATH=/some/path.json` to wire the breaker to
  that path. The file is created on first persistence with `0600`
  permissions.
- Leave `BREAKER_STATE_PATH` empty (the default) to keep the
  legacy pipeline unchanged. Existing systemd units that never set
  the variable see no behavior change.
- The orchestrator also accepts an explicit
  `circuit_breaker=...` injection on `JobTrailAutomation(...)` for
  tests and special deployments. The injection wins over the
  config-derived default; both ultimately drive the same
  `should_attempt()` / `record_*` / `try_alert()` contract.

## Pre-import deduplication (seen cache)

The automation launcher skips offers whose `(source, sourceJobId)` pair was
already imported within the configured TTL window. The cache lives outside the
repository in a JSON file with `0600` permissions:

- Default path: `<runtime-root>/jobtrail/logs/automated-job-search/seen.json`.
- Override with `JOBTRAIL_SEEN_CACHE_PATH=/absolute/path/to/seen.json`.
- TTL: `max(now - first_seen, hours_old * 2)`; entries older than
  `2 * JOB_SEARCH_HOURS_OLD` hours are considered expired and will be
  re-imported on the next run.

The cache is rewritten atomically via `tmp + rename`. A corrupted, missing, or
unreadable file degrades to an empty cache; the run continues without
deduplication and never aborts because of the cache. Cache failures appear on
the run summary as `seen-cache:check:<reason>` or `seen-cache:write:<reason>`.

### Bypass and reset

- `--reset-seen-cache` clears the cache before the run so every offer is
  re-imported (forces a one-shot re-import).
- `JOBTRAIL_RESET_SEEN_CACHE=1` (or `true`/`yes`/`on`) triggers the same reset.
- To disable the cache for a single run, point `JOBTRAIL_SEEN_CACHE_PATH` at a
  throwaway file and pass `--reset-seen-cache`.

The cache is always consulted before `POST /api/discover/import`; the backend
never sees offers that the cache reports as already-seen within the TTL.

## Bounded retries with backoff

Idempotent network calls (`/api/discover/search`, `GET /api/jobs/...`, and the
local scorer subprocess) are wrapped in a bounded retry helper
(`jobtrail_ai_scorer.retry`). `POST /api/discover/import` is non-idempotent, so a
lost response must not trigger a duplicate import and it is attempted only once.
Retries are also skipped for operations that could produce duplicate side effects
(`POST /api/jobs/.../notes`). There is no import retry opt-in because no verified
idempotency-key contract exists.

The retry helper uses an exponential schedule with a hard cap:

- Default policy: `max_attempts=3`, `base_delay=0.5s`, `max_delay=8.0s`.
- The delay is `min(base_delay * 2 ** attempt_index, max_delay)` so backoff
  stays bounded even for long-running transient outages.
- `jitter=False` by default. Enable it per-client when de-correlating parallel
  runs.

### Retry classification

The helper classifies each exception before deciding whether to retry:

- **Retryable (normally retried):** HTTP `5xx`, network/timeout failures,
  and `CalledProcessError` from the scorer subprocess. The non-idempotent
  import endpoint is classified from the same exception types but is not
  retried. For retried operations, the previous attempt's delay is logged
  with the structured prefix `retry:` so operators can grep the journal.
- **Exhausted:** every retry attempt failed; the helper re-raises the last
  exception with `retry_metadata={"attempts": N, "classification": "exhausted"}`.
  The orchestration records it on `AutomationRun.failures` as
  `<stage>[:<job_id>]:exhausted:<ExceptionType>`.
- **Terminal (no retry):** HTTP `4xx`, `ValueError`, `KeyError`,
  `FileNotFoundError`, `PermissionError`. The orchestration records it as
  `<stage>[:<job_id>]:terminal:<ExceptionType>`.

### Partial-success reporting

`AutomationRun.failures` carries the classification for every stage
(`search`, `import`, `score:<job_id>`, `read:<job_id>`, `seen-cache:...`).
Operators can grep the summary for `:retryable`, `:exhausted`, or `:terminal`
to distinguish transient blips from persistent failures. The summary never
includes exception messages, descriptions, profiles, prompts, or credentials.

## Daily WhatsApp summary fields

Every best-match notification produced by `JobTrailAutomation.run` is built by
`jobtrail_ai_scorer.notify.NotificationBuilder`. The rendered summary exposes
only the allowlisted fields below. Legacy required fields remain present when
source data is available; optional fields may be omitted when their source data is
absent:

| Field | Source | Notes |
| --- | --- | --- |
| `title` | `job.position` / `job.title` | Clipped to `MAX_TEXT_LENGTH` (200) characters. |
| `company` | `job.company` | Clipped. |
| `location` | `job.location` | Clipped. |
| `score` | `score.score` | The integer validated by `ScoreResult`. |
| `recommendation` | `score.recommendation` | Always one of `PRIORITY_APPLY`, `APPLY`, `REVIEW`, `SKIP`; defaults to `APPLY` when missing or unknown. |
| `recommendationLabel` | derived | Human-readable label (`"Priority Apply"`, `"Apply"`, `"Review"`, `"Skip"`). |
| `strengths` | `score.strengths` | First `MAX_LIST_ITEMS` (5) entries, each clipped. |
| `gaps` | `score.gaps` | First `MAX_LIST_ITEMS` (5) entries, each clipped. |
| `jobUrl` | `job.jobUrl` / `job.job_url` | External apply URL. |
| `jobTrailLink` | derived | `base_url` (after `JOBTRAIL_BASE_URL` resolution) joined with `/jobs/<id>`, percent-encoded. Omitted when the job has no id. |
| `runId` | derived | `YYYY-MM-DD-HHMM-<6-char hex>` for the daily run; deterministic for the same stamp + seed. |
| `searchProfiles` | selected profile provenance | Optional public profile names for the selected job only; first 5 entries, each clipped to 200 characters and scrubbed like every other string. |

### URL shortener (opt-in)

Set `WHATSAPP_SHORT_URL_BASE=https://sho.rt` (or another HTTPS host) to
rewrite the JobTrail host while preserving the trailing `/jobs/<id>` path.
The shortener is strictly opt-in: when the environment variable is unset
or empty, the unshortened `jobTrailLink` is rendered verbatim. A trailing
slash on `WHATSAPP_SHORT_URL_BASE` is tolerated without doubling the path.

### Redaction guarantees

`NotificationBuilder` scrubs every emitted string against the public
`FORBIDDEN_TOKENS` tuple (private runtime paths plus the `RESUME_SENTINEL`,
`PROFILE_SENTINEL`, `PROMPT_SENTINEL`, and `CREDENTIAL_SENTINEL` test-visible
sentinels). Real CV / profile / prompt / description / notes / reasoning /
credential values are never copied into the rendered summary because only
the allowlisted fields are emitted. The redaction is defensive: any string
that *does* end up in an allowlisted field is run through the scrub step
so a regression that leaks a sensitive substring is caught and replaced
with `[REDACTED]` before the helper message reaches WhatsApp.

### Tests

`tests/test_notify.py` asserts:

* the three new fields are always present when the corresponding source data
  is available;
* `runId` matches `^\d{4}-\d{2}-\d{2}-\d{4}-[a-f0-9]{6}$`;
* the recommendation is normalized to one of the four allowed values, with
  `APPLY` as the default;
* the allowlist is closed — no extra fields ever appear;
* every forbidden CV / profile / prompt / credential sentinel is stripped
  from the rendered output;
* `WHATSAPP_SHORT_URL_BASE` (set or unset) rewrites the JobTrail link
  correctly and tolerates a trailing slash.

`tests/test_automation.py` covers the end-to-end wiring through
`JobTrailAutomation.run` so the contract is guaranteed across the
orchestration boundary, not just in unit tests.

### WHATSAPP_NOTIFY_ON_FAILURE opt-in

`WHATSAPP_NOTIFY_ON_FAILURE=1` (or `true`/`yes`/`on`) appends a bounded
failure summary to the WhatsApp helper message when the run finishes with at
least one failure. It is independent of `WHATSAPP_NOTIFY_ENABLED`:

- `notify_enabled=False`, `notify_on_failure=True`: a failure summary is sent
  only when failures were recorded.
- `notify_enabled=True`, `notify_on_failure=False`: the existing best-match
  notification is sent when a match is selected (unchanged behavior).
- `notify_enabled=True`, `notify_on_failure=True`: when both apply, the
  failure summary is appended on a separate line after the best-match JSON so
  the match payload stays diff-friendly.

The summary exposes only the failure count and the first five abstract labels.
It never includes descriptions, profiles, prompts, notes, credentials, or
secrets. Default is off; opt in explicitly.

## Safety rules

- **Dry-run first:** `SCORER_DRY_RUN=1` applies to the scorer-only runner; use
  `JOBTRAIL_AUTOMATION_DRY_RUN=1` or the launcher `--dry-run` for the full automation
  search/import/score/notify plan. Keep the applicable dry-run setting enabled until the
  config, JobTrail connection, provider, and logs look correct.
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
cp scripts/scorer-config.example.yaml config.yaml
cp candidate-profile.example.md candidate-profile.md
cp scripts/hermes-docker-wrapper.example.sh ./hermes-docker-wrapper.local.sh
cp scripts/automated-job-search.example.py ./automated-job-search.local.py
cp scripts/notify-whatsapp-via-hermes.example.sh ./notify-whatsapp-via-hermes.local.sh
chmod +x ./hermes-docker-wrapper.local.sh ./automated-job-search.local.py ./notify-whatsapp-via-hermes.local.sh
```

`scripts/run-scorer.example.sh` no longer exists at that path; it moved to
[`scripts/legacy/run-scorer.example.sh`](scripts/legacy/README.md) and is
deprecated in favor of `scripts/automated-job-search.example.py` above (see
the deprecation note at the top of this document).

Edit the copied files or environment variables for your host. Keep public/example values in committed examples; put real local paths, profile text, and credentials only in ignored local files or environment variables.

## Canonical example layout and purge helper

The repo ships a single canonical config example at
`scripts/scorer-config.example.yaml`. The repo-root `config.example.yaml` is a
thin pointer to that file; do not duplicate the YAML settings between them. A
leak-detector test (`tests/test_example_redaction.py`) scans every committed
example and template for private IPv4 ranges (`10.x`, `172.16-31.x`,
`192.168.x`), private runtime path fragments, and credential prefixes
(`sk-…`, `ghp_…`, `gho_…`, `github_pat_…`, `xox[abprs]-…`) and fails CI on any
regression. A historical duplicate of the example lived at
`<runtime-root>/jobtrail/scorer.config.example.yaml` and contained an embedded
private IP. Remove it with the strict opt-in helper:

```sh
python3 scripts/_purge_runtime_example.py \
    --runtime-example "<runtime-root>/jobtrail/scorer.config.example.yaml" \
    --yes
```

Safety properties of `scripts/_purge_runtime_example.py`:

- Refuses to act without `--yes` (the script never deletes anything by accident).
- Refuses any target whose basename is not exactly
  `scorer.config.example.yaml` (so a typo cannot delete a CV, profile, or
  unrelated config).
- Requires an absolute path (relative paths cannot accidentally point at
  `config.yaml` in the current working directory).
- Refuses symlinks, directories, and missing files (the script never follows
  links or walks directories).
- Uses `os.remove` on the explicit file only — no shell-out, no recursive
  delete, no globbing.

Run the helper without `--yes` to see the refusal without touching the
filesystem:

```sh
python3 scripts/_purge_runtime_example.py \
    --runtime-example "<runtime-root>/jobtrail/scorer.config.example.yaml"
# refusing to remove … without --yes; pass --yes to confirm.
```

The helper is exercised end-to-end by `tests/test_runtime_layout.py`, which
asserts all of the safety properties above against a temporary file so the
tests never touch operator data.


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

### Prompt context budgets

The scorer bounds each local context section before constructing the provider prompt:

- candidate profile: 12,000 characters by default;
- optional candidate CV: 16,000 characters by default.

Set `PROMPT_PROFILE_BUDGET` or `PROMPT_CV_BUDGET` to a positive integer to override a
budget for one run. When a section is clipped, the default marker `\n[... content truncated ...]`
is appended; override it with `PROMPT_TRUNCATE_MARKER`. The marker counts toward the section's
budget. If a profile or CV is missing, a directory, or unreadable, the scorer logs a warning and
continues with the context that could be loaded. It does not expose file contents in the warning.

### Per-section token estimate and budget warning

Every `score` run emits exactly one `prompt_tokens_estimate={...}` line with a per-section
breakdown (`profile`, `cv`, `job`, `schema`, `instructions`, `total`). The estimate uses a
deterministic, dependency-free approximation:

- `PROMPT_TOKEN_ESTIMATOR=chars4` (default) rounds `len(text) / 4` up.
- `PROMPT_TOKEN_ESTIMATOR=words` splits on whitespace and counts tokens.

The estimate is reproducible for the same input so subsequent runs and CI logs can diff the
breakdown. The job section uses the same JSON serialization the provider receives, with notes
stripped. No external token counter or network call is involved.

Set `PROMPT_TOKEN_BUDGET` to a positive integer to enable the optional budget check. When
`total` exceeds the budget the scorer prints an extra `prompt_token_budget={"budget": N,
"total": M}` warning line in the same run. When the variable is unset, empty, or the total
fits, no warning is emitted. The warning never aborts the run and never embeds prompt,
profile, CV, or description content.

## Hermes Docker wrapper script

When Hermes is running in Docker, point `hermes_executable` at the copied Hermes Docker wrapper script. The example `scripts/hermes-docker-wrapper.example.sh` runs:

```sh
docker exec "$HERMES_CONTAINER" "$HERMES_BIN" "$@"
```

Configure `HERMES_CONTAINER` and `HERMES_BIN` in the scheduler environment if your container name or Hermes path differs.

## Runner usage

> **Deprecated.** The `run-scorer.example.sh` runner was moved under
> [`scripts/legacy/`](scripts/legacy/README.md) in Issue #8 and is kept only
> as a historical reference. New scheduler units must drive
> `scripts/automated-job-search.example.py` directly. The guidance below is
> retained verbatim so existing operators can keep their local `run-scorer.local.sh`
> copies running until they migrate; the wrapper itself is marked
> `DEPRECATED` and will be removed.

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

## Hermetic end-to-end tests

The repository ships an in-process end-to-end suite at
`tests/test_automation_e2e.py` that exercises the full
`search → import → score → notify` pipeline through the real
`JobTrailAutomation.run` orchestration code. The suite never reaches
out to Docker, systemd, or a real JobTrail backend; every external
dependency is replaced by a stub from `tests/stubs/`:

- `stubs.stub_jobtrail.StubJobTrailServer` runs an
  `http.server.BaseHTTPRequestHandler` on a random localhost port and
  imitates `/api/discover/search`, `/api/discover/import`, and
  `/api/jobs/<id>`. The handler is wired to a lock-protected
  `StubJobTrailState` so tests can assert on every request.
- `stubs.stub_jobspy.StubJobSpy` produces JobSpy-style search listings
  that the stub server returns verbatim from `POST /api/discover/search`.
- `stubs.stub_scorer.StubScorer` is a `Callable[[str, str], Any]` that
  writes a fixed `[AI_JOB_SCORE_V1]` note into the stub state, replacing
  the `SCORER_COMMAND` subprocess.
- `stubs.stub_whatsapp.StubWhatsApp` captures the WhatsApp helper message
  body to an in-memory buffer so tests can assert on the rendered output.

The stubs use only the standard library (`http.server`, `threading`,
`json`, `urllib.parse`); the orchestrator uses the real `httpx.Client`
and the production `RetryPolicy`, so the test exercises the genuine
HTTP boundary. The `conftest.py` adds `tests/` to `sys.path` so future
modules can `from stubs.stub_xxx import ...`.

### Required invariants

The suite asserts five invariant contracts required by Issue #11:

1. **Happy path** — `JobTrailAutomation.run` drives the full pipeline
   end-to-end: `searched`, `imported`, and `scored` counts match the
   number of listings returned by the in-process JobSpy; the captured
   WhatsApp message contains only the allowlisted fields and never
   embeds descriptions, candidate content, profile, prompt, or CV text.
2. **Dedup-skip-second-search** — a second run with the same
   `SeenCache` skips offers already imported within the TTL window;
   `imported == 0`, `scored == 0`, and no WhatsApp message is captured.
3. **Partial failure** — a transient 5xx, timeout, or connection error on one
   import is attempted once, surfaced as an `import:...` failure on the run
   summary, and does not stop the remaining offers from completing.
   When `notify_on_failure=True`, the bounded failure summary is the
   only message the WhatsApp helper receives.
4. **Redaction** — sentinel substrings in the scorer's
   `strengths`/`gaps` (`RESUME_SENTINEL`, `PROFILE_SENTINEL`,
   `PROMPT_SENTINEL`, `CREDENTIAL_SENTINEL`) are scrubbed by the
   production `NotificationBuilder` before the message reaches the
   WhatsApp buffer; `[REDACTED]` appears in the captured body.
5. **Single notification** — multiple scored jobs above the threshold
   still produce exactly one best-match notification, the
   `jobTrailLink` ends with `/jobs/<decoded id>`, and `runId` matches
   `^\d{4}-\d{2}-\d{2}-\d{4}-[a-f0-9]{6}$`.

A sixth triangulation test (`test_get_job_endpoint_serves_persisted_notes`)
verifies that notes written by the scorer survive a separate
`GET /api/jobs/<id>` request against the in-process server, proving the
read-back path is exercised through the real HTTP boundary.

### Running

```sh
python -m pytest tests/test_automation_e2e.py -v
# or, using the registered marker:
python -m pytest -m e2e -v
```

The suite takes a few seconds to run and requires no external services.
It uses the production `RetryPolicy(max_attempts=3, base_delay=0.0,
max_delay=0.0)` and a no-op `retry_sleep` so transient retries are
exercised without slowing CI.

## Hermes runtime policy guardrail (CI)

The runtime SOUL.md and `skills/jobtrail-automation/SKILL.md` live in the operator's local Hermes profile (for example under `<runtime-root>/hermes/profiles/job-search/`) and must NOT be committed to this repository. To keep the apply-gate contract reviewable, the repository ships CI-only fixture mocks under `tests/fixtures/hermes/` and a guardrail test suite (`tests/test_runtime_policy.py`) that runs on every PR and on a daily cron via `.github/workflows/policy.yml`.

The fixtures assert three rules and any drift fails CI with a focused diff:

- `SOUL.md` apply context contains the literal phrase `explicit confirmation`.
- `SKILL.md` contains the literal phrase `explicit confirmation`.
- Both files include either `never submit` or `without an explicit confirmation` in the apply section.

The fixtures must remain minimal contract mocks. They must NEVER embed private runtime paths, runtime path fragments that look like them, private CV/profile content, credentials, or any operator-only data. The leak guard (`test_fixtures_do_not_leak_runtime_data`) fails CI if such content sneaks in.

When the runtime SOUL/SKILL evolve locally, mirror the required phrases into the fixture files in the same PR so the policy contract stays auditable. The fixtures never need to mirror the full runtime content — only the apply-gate phrases and any new apply-section heading structure the guardrail needs.

To run the guardrail locally:

```sh
python -m pip install -e .
python -m pytest tests/test_runtime_policy.py -v
```

## Runtime install retention and cleanup

Use `scripts/runtime_install.py` to stage and install the `scorer-python` runtime. The default invocation is a dry run; pass `--yes` only after reviewing it. Changed installs retain exactly one sibling backup, `scorer-python.previous`; identical content is not rotated. Importing the helper never runs pip.

Historical backups can be removed only by explicitly naming them with `scripts/runtime_clean.py`. The command requires `--yes`, refuses symlinks, files, missing targets (unless `--missing-ok` is supplied), unrelated names, and targets outside an optional `--runtime-root`. It never discovers or removes unlisted paths.

```sh
python3 scripts/runtime_clean.py \\
  --runtime-root /absolute/runtime-root \\
  --target /absolute/runtime-root/scorer-python.previous \\
  --target /absolute/runtime-root/scorer-python.backup-automation-20260906T172527Z
# review the listed plan, then add --yes
```

## Optional Greenhouse source

`GreenhouseSourceAdapter` is enabled when `JOB_ATS_BOARDS.greenhouse_boards`
is non-empty. It issues one request per board to:

```text
GET https://boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true
```

Board scope wins over search terms and locations. Automation issues one ATS
request per configured search profile (not once per location), and results are
capped globally by the ATS `results_wanted`, malformed rows are skipped, and normalized jobs retain the
board slug, profile name, HTML-stripped content, remote heuristic, and UTC
retrieval timestamp. `4xx` responses are terminal; `5xx` and transport errors
use bounded retries. A failed Greenhouse search is recorded without blocking
JobSpy or Lever adapters.

### Funnel metrics

Use `jobtrail-ai-scorer metrics --period today|7d|30d --json` for a bounded privacy-safe view. `today` means the current UTC day; `7d` and `30d` are rolling half-open windows. Set `JOBTRAIL_RUN_JOURNAL_PATH` (or pass `--journal-path`) to enable journaling; it is disabled by default. Missing backend, journal, score, or application-status data is reported in `missing_data`; application status is limited to fields exposed by JobTrail.
