# Search Profiles Design

## Scope

Add public search profile provenance to automated search without changing source providers or exposing private runtime data.

## Configuration

`JOB_SEARCH_PROFILES` is an optional JSON array. Each entry accepts only `name`, `search_terms`, `sites`, `locations`, `results_wanted`, and `hours_old`. `name` is required, non-blank, unique, and public. Missing optional values fall back to the legacy `JOB_SEARCH_*` settings. If `JOB_SEARCH_PROFILES` is unset, legacy behavior is unchanged.

The profile JSON must not contain secrets, credentials, private paths, candidate profile/CV content, prompt text, or new source-provider definitions. `sites` only selects existing supported source names.

## Notification contract

Best-match notifications keep the existing required allowlisted fields. `searchProfiles` is optional and appears only when selected-job provenance exists. It is bounded to the first 5 public names, each clipped to 200 characters, and then processed by the existing notification scrubber.

## Output contract

The launcher final output includes `profile_counts` from `AutomationRun`: `searched`, `imported`, `duplicates`, and `failures` per profile when configured, `{}` otherwise.
