# Safe Manual Jobright Import Design

## Goal

Provide a deterministic, confirmation-gated CLI flow for importing manually supplied Jobright results without scraping, browser automation, autofill, recruiter messaging, or automatic application submission.

## User flow

```text
jobright URL + optional fields
        ↓
validate and normalize
        ↓
sanitary preview
        ↓
explicit --confirm gate
        ↓
one import POST
        ↓
cache identity + normal scoring policy + optional notification
```

The command is exposed as `jobtrail-ai-scorer import-jobright`. A Jobright URL is mandatory. Title, company, location, and description may be supplied by flags, stdin, or concise interactive prompts. Missing required values are requested interactively; non-interactive invocations fail clearly rather than inventing data.

The command always shows a preview before mutation. The preview contains only URL, title, company, location, fixed source `jobright_manual`, and whether scoring will run. It never prints the full description. `--confirm` is required to mutate; without it, the command performs no import, cache update, scoring, note write, or WhatsApp send. `--dry-run` dominates `--confirm` and guarantees no side effects.

## Data contract

- `source` is fixed to `jobright_manual`.
- `sourceJobId` is derived deterministically from the normalized Jobright URL.
- URL validation requires HTTPS and rejects malformed or empty URLs.
- The import payload uses the existing normalized source-adapter contract.
- Description is private scorer input only and is never included in public notification fields.

## Duplicate and failure behavior

Before mutation, the flow checks for an existing `(source, sourceJobId)` identity. A duplicate produces a clear result with the existing record and performs no second POST. The successful import then records the identity in `SeenCache`; cache updates happen only after the backend confirms the import.

The import POST is non-idempotent and is never automatically retried. If it fails, no cache, score note, lifecycle event, or notification is produced. If scoring fails after a successful import, the imported record is retained, a bounded error is reported, and no alert is sent. The flow never calls an application endpoint or transitions lifecycle state to `applied`.

## Architecture

1. Add a narrow manual-import command in `src/jobtrail_ai_scorer/main.py`.
2. Reuse the existing JobTrail HTTP import boundary and normalized job payload shape rather than adding a Jobright scraper or provider client.
3. Add a small validated input/normalization helper for URL identity and private field handling.
4. Reuse the existing scorer, CV/profile, threshold, notification, `SeenCache`, and failure semantics after confirmed import.
5. Keep the confirmation gate above every mutating operation and make interactive prompting injectable for tests.

## Privacy and safety

The command accepts only user-supplied Jobright content. It does not fetch or scrape Jobright. Descriptions are held in memory for scorer input and are excluded from previews, logs, errors, and WhatsApp summaries. Notification construction remains behind `NotificationBuilder`.

No automatic application submission, autofill, recruiter messaging, browser control, or lifecycle `applied` transition is implemented.

## Testing

- URL-only interactive follow-up and complete non-interactive input.
- Preview/no-confirmation proving zero POST, cache, scoring, note, and notification side effects.
- Confirmed import payload with `jobright_manual` provenance and stable source ID.
- Dry-run overriding confirmation.
- Malformed URL, missing fields, non-interactive input, and backend failure.
- Duplicate detection with no second POST.
- Privacy/redaction proving description, CV, prompts, credentials, and private URLs do not reach preview/log/WhatsApp output.
- Successful scoring and thresholded notification using the existing policy.
- Scoring failure retaining the import but suppressing notification.
- No application/lifecycle endpoint calls.
