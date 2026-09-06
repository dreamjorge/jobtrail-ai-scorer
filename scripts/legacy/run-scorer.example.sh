#!/usr/bin/env bash
# DEPRECATED — do not use in production.
# ---------------------------------------------------------------------------
# Filename:    scripts/legacy/run-scorer.example.sh
# Status:      Deprecated dry-run batch wrapper.
# Replacement: scripts/automated-job-search.example.py
#              (canonical orchestration entry point for the real flow).
# Target removal date: 2026-06-01
# Rationale:   The repo now ships a single canonical automation launcher.
#              This script is kept only as a historical reference for
#              operators migrating from the original dry-run-first setup.
# ---------------------------------------------------------------------------
set -euo pipefail

: "${SCORER_CONFIG_PATH:?SCORER_CONFIG_PATH must be set}"
SCORER_COMMAND="${SCORER_COMMAND:-jobtrail-ai-scorer}"
SCORER_LIMIT="${SCORER_LIMIT:-1}"
SCORER_DRY_RUN="${SCORER_DRY_RUN:-1}"
SCORER_LOG_DIR="${SCORER_LOG_DIR:-/tmp/jobtrail-ai-scorer-logs}"

if [[ -f "$SCORER_CONFIG_PATH" ]]; then
  :
else
  echo "SCORER_CONFIG_PATH does not exist or is not a file: $SCORER_CONFIG_PATH" >&2
  exit 2
fi

case "$SCORER_DRY_RUN" in
  1|true|yes)
    scorer_dry_run_enabled=1
    ;;
  0|false|no)
    scorer_dry_run_enabled=0
    ;;
  *)
    echo "Invalid SCORER_DRY_RUN: $SCORER_DRY_RUN. accepted values are 1, true, yes for dry-run or 0, false, no for real writes." >&2
    exit 2
    ;;
esac

mkdir -p "$SCORER_LOG_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_path="$SCORER_LOG_DIR/scorer-$timestamp.log"

echo "Running JobTrail AI scorer"
echo "Config: $SCORER_CONFIG_PATH"
echo "Limit: $SCORER_LIMIT"
echo "Dry run: $SCORER_DRY_RUN"
echo "Log: $log_path"

set +e
if [[ "$scorer_dry_run_enabled" == "1" ]]; then
  "$SCORER_COMMAND" score --config "$SCORER_CONFIG_PATH" --limit "$SCORER_LIMIT" "--dry-run" 2>&1 | tee "$log_path"
  scorer_exit="${PIPESTATUS[0]}"
else
  "$SCORER_COMMAND" score --config "$SCORER_CONFIG_PATH" --limit "$SCORER_LIMIT" 2>&1 | tee "$log_path"
  scorer_exit="${PIPESTATUS[0]}"
fi
set -e

if [[ "$scorer_exit" -eq 0 ]]; then
  echo "Scorer completed successfully. Log written to: $log_path"
else
  echo "Scorer failed with exit code $scorer_exit. Log written to: $log_path" >&2
fi

exit "$scorer_exit"
