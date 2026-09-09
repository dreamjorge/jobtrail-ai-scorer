# Full Automation Dry-Run (Issue #36)

## Goal

Add a consistent simulation mode for the complete automation flow. Search/discovery and score validation run against captured or live read-only data, while imports, score-note writes, application-state writes, cache/breaker/journal persistence, and WhatsApp notifications are blocked.

## Structured scorer preview

The scorer CLI gains a machine-readable preview mode:

```text
jobtrail-ai-scorer score --dry-run --json ...
```

It validates the provider response using the existing schema but writes no note. It emits one bounded JSON object per outcome containing only score, recommendation, strengths, gaps, career value, reasoning, hard requirements, and confirmation flags. No prompt, CV, profile, description, metadata, credentials, or raw provider output is emitted.

Automation invokes the scorer subprocess with `--dry-run --json` in dry-run mode and parses the object. Normal mode keeps its current subprocess arguments and output.

## Automation gates

`AutomationConfig.dry_run` is parsed from `JOBTRAIL_AUTOMATION_DRY_RUN`; launcher `--dry-run` overrides it.

When true:

- Search adapters run normally.
- `SeenCache` is read-only; no transaction lock or mark/write.
- `gateway.import_job` is never called.
- Scoring runs through structured scorer preview; no JobTrail note is saved.
- `get_job` readbacks are skipped because no JobTrail id exists.
- NotificationBuilder renders a bounded preview, but notifier is never called.
- Circuit breaker state and run journal are not mutated.
- `AutomationRun` reports planned counts and contains `dry_run`, `planned_operations`, and `notification_preview`.

Normal mode remains unchanged.

## Determinism and privacy

- JSON output uses sorted keys and stable operation ordering.
- Preview fields reuse NotificationBuilder's allowlist/scrubbing. Raw descriptions, notes, metadata, CV/profile contents, URLs beyond the allowlisted public job link, and credentials are absent.
- Tests use fixed clocks and fake subprocess/scorer output.
