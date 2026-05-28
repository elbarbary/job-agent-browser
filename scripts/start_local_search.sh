#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$ROOT/data/search/searxng"
PORT="${JOB_AGENT_SEARXNG_PORT:-8099}"
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  DOCKER=(sudo -S docker)
fi
mkdir -p "$CONFIG_DIR"
chmod 700 "$ROOT/data" 2>/dev/null || true
chmod 755 "$ROOT/data/search" "$CONFIG_DIR" 2>/dev/null || true

SETTINGS="$CONFIG_DIR/settings.yml"
if [[ ! -f "$SETTINGS" ]]; then
  SECRET="$(openssl rand -hex 32 2>/dev/null || python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  cat > "$SETTINGS" <<YAML
use_default_settings: true

server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "$SECRET"
  limiter: false
  image_proxy: false

search:
  formats:
    - html
    - json
YAML
  chmod 644 "$SETTINGS"
fi
chmod 755 "$CONFIG_DIR" 2>/dev/null || true
chmod 644 "$SETTINGS" 2>/dev/null || true

"${DOCKER[@]}" rm -f job-agent-searxng >/dev/null 2>&1 || true
"${DOCKER[@]}" run -d \
  --name job-agent-searxng \
  --restart unless-stopped \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  -p 127.0.0.1:${PORT}:8080 \
  -v "$CONFIG_DIR:/etc/searxng:rw" \
  -e SEARXNG_BASE_URL="http://127.0.0.1:${PORT}/" \
  searxng/searxng:latest

echo "Local SearXNG started on http://127.0.0.1:${PORT}"
