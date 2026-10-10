#!/bin/zsh

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
SESSION_NAME="gold-monitor"
HOST="0.0.0.0"
PORT="8000"
PID_PATTERN="uvicorn app.main:app --host ${HOST} --port ${PORT}"

is_running() {
  pgrep -f "$PID_PATTERN" >/dev/null 2>&1
}

start_service() {
  if is_running; then
    echo "Service is already running."
    return 0
  fi

  screen -dmS "$SESSION_NAME" zsh -lc "cd '$REPO_ROOT' && source .venv/bin/activate && exec uvicorn app.main:app --host $HOST --port $PORT"
  sleep 1

  if is_running; then
    echo "Service started successfully."
  else
    echo "Service failed to start."
    return 1
  fi
}

stop_service() {
  local stopped=0

  if is_running; then
    pkill -f "$PID_PATTERN" || true
    stopped=1
  fi

  screen -S "$SESSION_NAME" -X quit >/dev/null 2>&1 || true

  if is_running; then
    echo "Service is still running, please check manually."
    return 1
  fi

  if [[ $stopped -eq 1 ]]; then
    echo "Service stopped successfully."
  else
    echo "Service is not running."
  fi
}

status_service() {
  if is_running; then
    echo "Service status: running"
    pgrep -fl "$PID_PATTERN"
  else
    echo "Service status: stopped"
  fi
}

case "${1:-}" in
  start)
    start_service
    ;;
  stop)
    stop_service
    ;;
  restart)
    stop_service
    start_service
    ;;
  status)
    status_service
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
