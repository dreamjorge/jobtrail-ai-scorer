# Search Profiles Implementation Plan

## Implemented slices

- Parse optional `JOB_SEARCH_PROFILES` JSON with a closed set of public keys.
- Expand search requests per profile while preserving the legacy fallback when unset.
- Track per-profile counts and duplicate provenance.
- Include selected-job public `searchProfiles` provenance in best-match notifications when present.
- Keep notification output bounded and scrubbed through `NotificationBuilder`.
- Include `profile_counts` in the launcher final output.
- Document operator configuration, safety constraints, and notification fields.

## Validation

Focused PR3 validation:

```sh
python -m pytest tests/test_automation.py::test_selected_notification_includes_public_search_profiles tests/test_notify.py tests/test_automation_e2e.py tests/test_automated_job_search_launcher.py -q
```

Full repository validation:

```sh
python -m pytest -q
```
