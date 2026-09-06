# Legacy scripts

This directory groups scripts that **do not use in production** and are kept
only for historical reference. Every file under `scripts/legacy/` starts with
a `DEPRECATED` header that names its replacement and the target removal date.

## What lives here

| Script | Replacement | Why it is deprecated |
| --- | --- | --- |
| `run-scorer.example.sh` | `scripts/automated-job-search.example.py` | Batch dry-run wrapper predates the canonical orchestration launcher; the canonical flow now resolves the backend URL, deduplicates seen offers, and wraps every retryable call with bounded backoff. |
| `hermes-score-jobs.sh` | `scripts/automated-job-search.example.py` | One-shot Hermes `job-search` dry-run wrapper; the canonical flow drives Hermes via the same launcher with `--dry-run` semantics built into the CLI. |

## Operator guidance

- **Do not** copy these scripts into a scheduler unit. Use
  [`scripts/automated-job-search.example.py`](../../scripts/automated-job-search.example.py)
  (the canonical flow) and, where appropriate, the existing
  `scripts/hermes-docker-wrapper.example.sh` and
  `scripts/notify-whatsapp-via-hermes.example.sh` helpers.
- These files may be removed after the target removal date listed in their
  header. Until then they remain committed for operators who still reference
  the historical dry-run setup.
- See [`docs/runtime-automation.md`](../../docs/runtime-automation.md) for the
  current end-to-end automation guide.