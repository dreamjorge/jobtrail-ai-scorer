#!/usr/bin/env bash
# DEPRECATED — do not use in production.
# ---------------------------------------------------------------------------
# Filename:    scripts/legacy/hermes-score-jobs.sh
# Status:      Deprecated Hermes ``job-search`` dry-run wrapper.
# Replacement: scripts/automated-job-search.example.py
#              (canonical orchestration entry point for the real flow).
# Target removal date: 2026-06-01
# Rationale:   The repo now ships a single canonical automation launcher
#              that resolves the backend URL, deduplicates seen offers, and
#              scores imported jobs through Hermes. This wrapper only ran a
#              one-shot Hermes ``job-search`` dry run and is kept here for
#              historical reference only.
# ---------------------------------------------------------------------------
set -euo pipefail

: "${HERMES_EXECUTABLE:?HERMES_EXECUTABLE must be set to a Hermes CLI wrapper}"

if ! command -v "$HERMES_EXECUTABLE" >/dev/null 2>&1; then
  echo "HERMES_EXECUTABLE not found on PATH: $HERMES_EXECUTABLE" >&2
  exit 127
fi

echo "DEPRECATED: hermes-score-jobs.sh is a historical dry-run wrapper." >&2
echo "DEPRECATED: do not use in production. Use scripts/automated-job-search.example.py instead." >&2

"$HERMES_EXECUTABLE" --profile job-search --dry-run "$@"