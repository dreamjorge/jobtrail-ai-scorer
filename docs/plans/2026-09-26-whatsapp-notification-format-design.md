# WhatsApp Notification Formatting Design

## Goal
Render selected JobTrail matches as readable WhatsApp text instead of compact JSON while preserving the existing bounded, redacted notification data contract.

## Scope
- Change only the selected-match rendering path in `src/jobtrail_ai_scorer/automation.py`.
- Preserve score, recommendation, links, run ID, bounded strengths/gaps, and redaction behavior.
- Omit empty optional fields such as location.
- Keep the no-match and failure message paths unchanged.
- Do not alter URL accessibility or runtime configuration in this change; an internal JobTrail URL remains a deployment concern.

## Rendering
The message will contain a title header, score/recommendation, available JobTrail and source links, bounded strengths, bounded gaps, and the run ID. Lists use WhatsApp-friendly bullets. Empty values are omitted. The output must not be valid compact JSON and must remain deterministic for tests.

## Error and privacy behavior
The formatter consumes the already allowlisted/redacted summary produced by `build_notification_summary`; it introduces no new source fields or private data. Missing or empty values are skipped rather than rendered as blank labels.

## Verification
Add focused tests covering readable rendering, omission of empty location, preservation of links and score, and bounded list output. Run the focused automation tests and the existing scorer test suite.
