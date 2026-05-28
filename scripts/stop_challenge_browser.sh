#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/data/logs/runtime/challenge-browser"

stop_pid() {
  local name="$1"
  local pid_file="$RUNTIME_DIR/${name}.pid"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 0.5
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  fi
}

stop_pid chrome
stop_pid novnc
stop_pid x11vnc
stop_pid openbox
stop_pid xvfb

if [[ -f "$RUNTIME_DIR/restart-worker-on-stop" ]]; then
  rm -f "$RUNTIME_DIR/restart-worker-on-stop"
  systemctl --user start job-agent-browser.service 2>/dev/null || true
fi

echo "Challenge browser stopped. Worker restart requested if it had been paused."
