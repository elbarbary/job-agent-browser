#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OLLAMA_BIN="${OLLAMA_BIN:-$HOME/.local/bin/ollama}"
OLLAMA_MODELS="${OLLAMA_MODELS:-$HOME/.local/share/ollama/models}"
LOG_DIR="$ROOT/data/logs/runtime"
mkdir -p "$LOG_DIR" "$OLLAMA_MODELS"
chmod 700 "$LOG_DIR" "$OLLAMA_MODELS"

if [[ ! -x "$OLLAMA_BIN" ]]; then
  printf 'Ollama not found at %s\n' "$OLLAMA_BIN" >&2
  exit 1
fi

if curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  printf 'Ollama is already serving at http://127.0.0.1:11434\n'
  exit 0
fi

OLLAMA_HOST=127.0.0.1:11434 \
OLLAMA_MODELS="$OLLAMA_MODELS" \
OLLAMA_KEEP_ALIVE=5m \
OLLAMA_NO_CLOUD=true \
nohup "$OLLAMA_BIN" serve > "$LOG_DIR/ollama.log" 2>&1 &
printf '%s\n' "$!" > "$LOG_DIR/ollama.pid"
chmod 600 "$LOG_DIR/ollama.pid" "$LOG_DIR/ollama.log"
sleep 2
curl -fsS http://127.0.0.1:11434/api/version
printf '\nOllama is bound to loopback only.\n'
