#!/usr/bin/env bash
set -euo pipefail

WHATSAPP_NOTIFY_ENABLED="${WHATSAPP_NOTIFY_ENABLED:-}"
HERMES_PROFILE="${HERMES_PROFILE:-job-search}"
HERMES_EXECUTABLE="${HERMES_EXECUTABLE:-hermes}"

if [[ "$WHATSAPP_NOTIFY_ENABLED" != "1" ]]; then
  echo "WhatsApp notification disabled; set WHATSAPP_NOTIFY_ENABLED=1 to send."
  exit 0
fi

if [[ "$#" -gt 0 ]]; then
  SUMMARY_TEXT="$*"
else
  SUMMARY_TEXT="$(cat)"
fi

if [[ -z "${SUMMARY_TEXT//[[:space:]]/}" ]]; then
  echo "Summary text is required via arguments or stdin." >&2
  exit 2
fi

PROMPT="$(cat <<EOF
Send this JobTrail AI scorer summary through the already-connected WhatsApp integration.
Use only this summary and log metadata. Do not include private source text, credentials, or expanded application materials.

Summary:
$SUMMARY_TEXT
EOF
)"

"$HERMES_EXECUTABLE" --profile "$HERMES_PROFILE" -z "$PROMPT" --cli
