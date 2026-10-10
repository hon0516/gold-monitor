#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
ENV_FILE="${GOLD_MONITOR_ENV_FILE:-$REPO_ROOT/.env.production}"
HOST="${GOLD_MONITOR_HOST:-0.0.0.0}"
PORT="${GOLD_MONITOR_PORT:-8000}"

cd "$REPO_ROOT"
mkdir -p "$REPO_ROOT/logs" "$REPO_ROOT/data"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

exec "$REPO_ROOT/.venv/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT"
