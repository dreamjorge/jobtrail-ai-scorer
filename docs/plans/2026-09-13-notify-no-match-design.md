# No-Match WhatsApp Notification Design

## Goal

Notify the configured WhatsApp recipient with a short run summary when a real automation run completes without a qualifying job.

## Scope

- Apply only to the real automation pipeline.
- Reuse the existing `WHATSAPP_NOTIFY_ENABLED` gate, configured helper, and target.
- Do not send from automation dry-run mode.
- Preserve the existing selected-job and failure notification behavior.
- Record the outcome as `notification_kind: "no_match"` in the run journal.

## Message

```text
JobTrail — sin coincidencias
Buscadas: <searched>
Puntuadas: <scored>
Umbral: <score_threshold>
No hubo ofertas que calificaran.
```

The implementation uses the repository's existing technical-message convention unless an existing localized message builder requires otherwise. It contains counts and the configured threshold only; it does not include candidate profile, CV, prompts, or job payloads.

## Data Flow

1. `JobSearchAutomation.run()` finishes discovery and scoring.
2. If no job is selected, no failure exists, notifications are enabled, and the run is not dry-run, `_compose_notification()` returns the no-match summary.
3. The existing notifier invokes the configured WhatsApp helper.
4. `AutomationRun.notification_sent` records whether the helper succeeded.
5. `run_journal.record_run()` classifies the outcome as `no_match`.

A notifier exception remains a `notify` failure and the process retains its existing failure/exit behavior.

## Alternatives Considered

1. **Always send no-match using the existing notification gate (selected).** Minimal configuration, directly matches the operator request, and keeps notification policy centralized.
2. **Add `WHATSAPP_NOTIFY_ON_NO_MATCH`.** More control but introduces a new setting for a behavior the operator explicitly requested.
3. **Send a periodic digest instead.** Reduces message volume but does not satisfy immediate run feedback and requires scheduling/state changes.

## Testing

- Unit test the exact no-match message and guard conditions.
- Test the real pipeline calls the notifier for a clean no-match run.
- Test dry-run still never calls the notifier.
- Test the journal classifies the run as `no_match` while preserving `notified`.
- Run the focused tests, full pytest suite, and Ruff.
