#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/data/logs/runtime/ollama.pid"
if [[ ! -f "$PID_FILE" ]]; then
  printf 'No project-managed Ollama PID file was found.\n'
  exit 0
fi
PID="$(cat "$PID_FILE")"
kill "$PID"
rm -f "$PID_FILE"
printf 'Stopped project-managed Ollama process %s.\n' "$PID"
