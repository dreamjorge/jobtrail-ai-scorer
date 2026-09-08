# ATS Connectors Design

## Goal

Implement issue #40 by adding optional Lever and Greenhouse ATS connectors behind the existing source-adapter contract.

## Configuration

`JOB_ATS_BOARDS` is a JSON object with a closed allowlist of keys:

```json
{
  "lever_boards": ["acme", "globex"],
  "greenhouse_boards": ["acmeco"],
  "results_wanted": 25
}
```

Allowed keys: `lever_boards`, `greenhouse_boards`, `results_wanted`. Unknown keys raise `ValueError`. Boards must be non-blank strings; duplicates inside the same list raise `ValueError`. `results_wanted` defaults to 25 and is bounded to `1..200`.

When `JOB_ATS_BOARDS` is unset or empty, no ATS adapters are constructed.

## Adapters

- `LeverSourceAdapter(name="lever")` issues one bounded GET per board: `GET https://api.lever.co/v0/postings/<board>?mode=json`. Posts are truncated to `min(len(rows), results_wanted)`.
- `GreenhouseSourceAdapter(name="greenhouse")` issues one bounded GET per board: `GET https://boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true`.

Both reuse the existing `RetryPolicy`/`retry_call`; 5xx retry, 4xx terminal. Adapter exceptions surface in `AutomationRun.failures` and other adapters/requests continue.

## Normalization

`LeverSourceAdapter` and `GreenhouseSourceAdapter` translate their provider payloads into `NormalizedJob` with `source` set to `"lever"` or `"greenhouse"`, identity `(source, source_job_id)` retained for dedupe, `search_profile=SourceSearchRequest.profile_name`, and `retrieved_at` stamped in UTC ISO-8601.

## Wiring

`JobTrailAutomation.__init__` defaults to:

```python
(JobSpySourceAdapter(gateway), *build_ats_adapters(config.ats_boards))
```

where `build_ats_adapters` returns empty when no ATS boards are configured. Existing tests that pass explicit `source_adapters` are preserved.

## Testing

- Parser tests: happy path, invalid JSON, unknown keys, blank/duplicate tokens, non-int `results_wanted`, oversized cap.
- Adapters: `httpx.MockTransport` for happy path, cap, profile propagation, retry/recovery, error isolation.
- Automation integration: ATS failure does not block JobSpy; default unchanged when no ATS configured.

## Non-goals

- No API keys.
- No pagination beyond the first public response.
- No new fields in `SourceSearchRequest`.
- `JOB_SEARCH_PROFILES.sites` does not select ATS providers.
