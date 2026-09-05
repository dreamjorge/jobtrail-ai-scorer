#!/usr/bin/env bash
set -euo pipefail

HERMES_CONTAINER="${HERMES_CONTAINER:-hermes}"
HERMES_BIN="${HERMES_BIN:-/opt/hermes/.venv/bin/hermes}"

exec docker exec "$HERMES_CONTAINER" "$HERMES_BIN" "$@"
