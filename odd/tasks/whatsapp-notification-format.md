# WhatsApp notification formatting

Status: implemented and verified; not committed (explicit commit authorization not provided).

## Intent
Render selected JobTrail match notifications as readable WhatsApp text instead of compact JSON.

## Constraints
- Preserve allowlisting, bounds, and redaction.
- Omit empty optional fields.
- Keep no-match/failure paths unchanged.
- Do not change runtime URLs or credentials.
- No commit/push/PR without explicit user authorization.

## Route
Delegated direct writer for the two-file behavior/test change (multi-file write trigger).

## Tasks
1. Add failing formatter tests.
2. Implement formatter and integrate it in selected-match path.
3. Run focused and regression verification.

## Evidence
Design: `docs/plans/2026-09-26-whatsapp-notification-format-design.md`
Plan: `docs/plans/2026-09-26-whatsapp-notification-format-plan.md`

Verification:
- `pytest -q tests/test_automation.py -k 'notification or compose'`: 13 passed, 88 deselected.
- `pytest -q tests/test_notify.py tests/test_automation.py`: 135 passed.
- `python -m compileall -q src`: passed.
