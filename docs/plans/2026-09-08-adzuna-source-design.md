# Adzuna Source Adapter Design

## Goal

Implement issue #39 by adding an optional Adzuna API source adapter behind the existing source-adapter contract introduced in #38.

## Architecture

Add a new module `src/jobtrail_ai_scorer/sources/adzuna.py` that implements the existing `SourceAdapter` protocol. `sources/__init__.py` re-exports `AdzunaSourceAdapter` so call sites can keep importing from `jobtrail_ai_scorer.sources`.

The adapter owns:

- Adzuna configuration object (app id, app key, country, base URL).
- An injectable HTTP gateway built on `httpx`.
- Translation from `SourceSearchRequest` to a single-page Adzuna GET request.
- Translation from Adzuna response items to `NormalizedJob`.

## Configuration

Credentials live only in the environment. No YAML or checked-in settings.

```bash
ADZUNA_APP_ID
ADZUNA_APP_KEY
ADZUNA_COUNTRY                # example: "mx"
ADZUNA_BASE_URL           # optional override, default https://api.adzuna.com/v1
JOB_DISABLE_ADZUNA            # optional explicit disable
```

Disable semantics:

- Missing `ADZUNA_APP_ID` or `ADZUNA_APP_KEY` keeps Adzuna disabled silently.
- `JOB_DISABLE_ADZUNA=1` forces disable regardless of credentials.
- A warning is logged with the exception type when partial credential setup is detected.
- Secrets are never logged.

## Request translation

For each `SourceSearchRequest` the adapter performs a single GET:

```
GET {base_url}/jobs/{country}/search/1?app_id=...&app_key=...&what={search_term}&where={location}&results_per_page={min(results_wanted, MAX_PER_PAGE)}&max_days_old={hours_old}
```

`results_wanted` is capped at `MAX_PER_PAGE` (default 50) so the adapter does not paginate in this slice. `is_remote` is currently a no-op for Adzuna.

## Normalization

| Adzuna field | `NormalizedJob` |
|---|---|
| result `id` | `source_job_id` |
| constant `"adzuna"` | `source` |
| `title` | `title` |
| `company.display_name` | `company` |
| `description` | `description` |
| `redirect_url` | `source_url` |
| `location.display_name` | `location` |
| `salary_min` / `salary_max` | `salary_min` / `salary_max` when present |
| configured country | `salary_currency` when known |
| `contract_type` / `contract_time` | `job_type` |
| request `profile_name` | `search_profile` |
| `created` | `retrieved_at` |

Missing fields are preserved as `None` and dropped by `NormalizedJob.to_import_payload()` per existing behavior.

## Retry and partial failure

- Reuse `RetryPolicy` and `retry_call` so transient transport errors and 5xx responses retry with bounded attempts.
- Treat 4xx responses as terminal; 429 may be revisited in a follow-up.
- Adapter raises typed exceptions that the orchestration loop catches and reports as bounded failure labels without leaking the secret or the request URL when it contains credentials.

## Testing

- `tests/test_adzuna.py` with `httpx.MockTransport` and local fixtures.
- Coverage:
  - happy path normalization (full nested record).
  - missing fields.
  - 4xx terminal.
  - 5xx retryable with eventual success.
  - missing credentials.
  - explicit disable.
- `tests/test_automation.py` integration: Adzuna failure does not block JobSpy import.

## Non-goals

- No pagination beyond one bounded page.
- No new fields in `SourceSearchRequest`.
- No changes to JobTrail backend.
- No secrets in logs, fixtures, or notifications.
