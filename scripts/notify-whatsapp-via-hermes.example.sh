#!/usr/bin/env bash
set -euo pipefail

WHATSAPP_NOTIFY_ENABLED="${WHATSAPP_NOTIFY_ENABLED:-}"
HERMES_WHATSAPP_TARGET="${HERMES_WHATSAPP_TARGET:-}"
HERMES_EXECUTABLE="${HERMES_EXECUTABLE:-hermes}"

if [[ "$WHATSAPP_NOTIFY_ENABLED" != "1" ]]; then
  echo "WhatsApp notification disabled; set WHATSAPP_NOTIFY_ENABLED=1 to send."
  exit 0
fi

if [[ -z "${HERMES_WHATSAPP_TARGET//[[:space:]]/}" ]]; then
  echo "HERMES_WHATSAPP_TARGET must be set to a non-empty local recipient when notifications are enabled." >&2
  exit 2
fi

if [[ "$#" -gt 0 ]]; then
  SUMMARY_TEXT="$*"
else
  # The sentinel keeps command substitution from stripping trailing newlines.
  SUMMARY_WITH_SENTINEL="$(cat; printf '\001')"
  SUMMARY_TEXT="${SUMMARY_WITH_SENTINEL%$'\001'}"
fi

if [[ -z "${SUMMARY_TEXT//[[:space:]]/}" ]]; then
  echo "Summary text is required via arguments or stdin." >&2
  exit 2
fi

# The message is already rendered and redacted by the caller. Send it directly,
# without asking an LLM to transform or reinterpret it.
printf '%s' "$SUMMARY_TEXT" | "$HERMES_EXECUTABLE" send --to "$HERMES_WHATSAPP_TARGET"
