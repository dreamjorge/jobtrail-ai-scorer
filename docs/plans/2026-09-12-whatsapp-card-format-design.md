# WhatsApp Detailed Card Formatting

## Goal

Improve JobTrail WhatsApp notifications from raw or flat summaries into a readable detailed card per qualifying offer, while preserving one-message-per-run, privacy, bounded output, and backward-compatible single-match behavior.

## User-visible design

Each selected offer is rendered as a bounded card:

```text
━━━━━━━━━━━━━━━━━━━━
🏆 #1 · 93/100 · PRIORITY
Software Engineer (AI Training)
🏢 Alignerr · 📍 Remote

✅ Fortalezas
• ...
• ...

⚠️ Brechas
• ...
• ...

🔗 Ver en JobTrail
🔗 Ver publicación
━━━━━━━━━━━━━━━━━━━━
```

The production notification language follows the existing user-facing convention. The card is intentionally scannable on a phone: score and recommendation lead, identity is immediately below, evidence is grouped into short lists, and links are separated for tapping.

For a single selected offer, the card omits the ranking marker when it would be redundant. For a multi-offer digest, cards are numbered in the deterministic selection order and separated within one WhatsApp message.

## Architecture

1. Keep candidate selection and `NotificationBuilder` as the only sources of notification data.
2. Add a deterministic renderer for one bounded summary and a digest renderer that joins cards with stable separators.
3. Reuse the existing allowlist, text clipping, list-item limits, forbidden-token scrubbing, and URL construction before rendering. The renderer must not access raw descriptions, CV/profile content, prompts, notes, credentials, or provider reasoning.
4. Make the notification script send the already-rendered text through Hermes direct `send`, avoiding an LLM formatting dependency. The configured WhatsApp target remains explicit and comes from deployment configuration.
5. Keep normal automation's one notifier invocation and dry-run's no-notification guarantee unchanged.

## Data flow

`qualified candidates -> deterministic top-N selector -> NotificationBuilder summaries -> card renderer -> one WhatsApp message`

The renderer accepts only the bounded summary shape. A missing or empty strengths/breaches list renders a short omission-safe line or is omitted; it never causes raw fallback content to leak.

## Error handling

- Invalid configuration remains a failed configuration parse, not a partially rendered notification.
- A missing optional field produces an omission-safe card.
- Rendering failure is reported as a notification failure and does not mutate job state.
- Hermes direct-send failures remain visible in the automation result and service logs.
- No fallback invokes an LLM with private candidate context.

## Testing

- Snapshot-like assertions for single-card and three-card output, including separators, ranking, and links.
- Deterministic output for equal-score candidates and reversed input order.
- Bounds, allowlist, and forbidden-token tests for every rendered card.
- Empty strengths/breaches and missing links.
- Exactly one notifier call for normal digest and zero notifier calls for dry-run.
- Direct sender script integration with a mocked Hermes executable.
- Full unit and e2e suites plus shell syntax/diff checks.

## Non-goals

- No application submission or lifecycle changes.
- No changes to score thresholds or top-N selection policy.
- No expanded public fields beyond the existing allowlist.
- No WhatsApp platform or target reconfiguration beyond using the already configured target.
