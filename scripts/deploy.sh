#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-8501}"
PID_FILE="$ROOT_DIR/data/.server.pid"
LOG_FILE="$ROOT_DIR/data/server.log"

PYTHON_BIN="python3"
if [[ -x "$ROOT_DIR/.venv/bin/python3" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python3"
fi

MODE="foreground"
RUN_TESTS=1

usage() {
  cat <<'EOF'
Usage: ./scripts/deploy.sh [options]

Options:
  --bg          Run server in background (writes PID to data/.server.pid, logs to data/server.log)
  --stop        Stop background server started with --bg
  --skip-tests  Skip running unit tests before starting the server
  -h, --help    Show this help message

Environment:
  PORT          Port to bind locally (default: 8501)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bg)
      MODE="background"
      shift
      ;;
    --stop)
      MODE="stop"
      shift
      ;;
    --skip-tests)
      RUN_TESTS=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

stop_bg_server() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "==> Stopping existing server (PID $pid)..."
      kill "$pid"
      for _ in $(seq 1 20); do
        if ! kill -0 "$pid" 2>/dev/null; then
          break
        fi
        sleep 0.1
      done
    fi
    rm -f "$PID_FILE"
  fi
}

if [[ "$MODE" == "stop" ]]; then
  stop_bg_server
  echo "==> Done."
  exit 0
fi

if [[ "$RUN_TESTS" -eq 1 ]]; then
  "$ROOT_DIR/scripts/test.sh"
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "Warning: .env not found in $ROOT_DIR. You can configure Strava credentials in the UI."
fi

mkdir -p "$ROOT_DIR/data"

if [[ "$MODE" == "background" ]]; then
  stop_bg_server
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Error: Port $PORT is already in use. Stop the existing process or pass PORT=<other>." >&2
    exit 1
  fi
  echo "==> Starting dashboard in background on http://localhost:$PORT ..."
  nohup env PORT="$PORT" "$PYTHON_BIN" "$ROOT_DIR/app.py" >"$LOG_FILE" 2>&1 &
  SERVER_PID=$!
  echo "$SERVER_PID" >"$PID_FILE"
  sleep 0.8
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "Error: Server failed to start. Check $LOG_FILE:" >&2
    cat "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE"
    exit 1
  fi
  echo "==> Running at http://localhost:$PORT (PID $SERVER_PID)"
  echo "    Logs: $LOG_FILE"
  echo "    Stop: ./scripts/deploy.sh --stop"
else
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Error: Port $PORT is already in use. Stop the existing process or pass PORT=<other>." >&2
    exit 1
  fi
  echo "==> Starting dashboard on http://localhost:$PORT ..."
  exec env PORT="$PORT" "$PYTHON_BIN" "$ROOT_DIR/app.py"
fi
