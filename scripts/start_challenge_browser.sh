#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPLAY_ID="${JOB_AGENT_CHALLENGE_DISPLAY:-:88}"
VNC_PORT="${JOB_AGENT_CHALLENGE_VNC_PORT:-5901}"
NOVNC_PORT="${JOB_AGENT_CHALLENGE_NOVNC_PORT:-6080}"
CDP_PORT="${JOB_AGENT_CHALLENGE_CDP_PORT:-9223}"
URL="${1:-https://mail.google.com/}"
RUNTIME_DIR="$ROOT/data/logs/runtime/challenge-browser"
PROFILE_DIR="$ROOT/data/sessions/browser-profile"
REMOTE_HOST="${JOB_AGENT_REMOTE_HOST:-100.116.208.74}"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing dependency: $1" >&2
    echo "Install with: sudo apt-get install -y xvfb x11vnc openbox novnc websockify" >&2
    exit 1
  fi
}

need Xvfb
need x11vnc
need openbox
need google-chrome
need websockify

mkdir -p "$RUNTIME_DIR" "$PROFILE_DIR"
chmod 700 "$RUNTIME_DIR" "$PROFILE_DIR"

if [[ -f "$RUNTIME_DIR/xvfb.pid" ]] && ! kill -0 "$(cat "$RUNTIME_DIR/xvfb.pid")" 2>/dev/null; then
  rm -f "$RUNTIME_DIR"/*.pid
fi

if systemctl --user is-active --quiet job-agent-browser.service 2>/dev/null; then
  systemctl --user stop job-agent-browser.service
  touch "$RUNTIME_DIR/restart-worker-on-stop"
fi

if [[ -f "$RUNTIME_DIR/xvfb.pid" ]] && kill -0 "$(cat "$RUNTIME_DIR/xvfb.pid")" 2>/dev/null; then
  echo "Challenge browser already appears to be running."
  echo "Open via SSH tunnel: ssh -N -L ${NOVNC_PORT}:127.0.0.1:${NOVNC_PORT} barbary@${REMOTE_HOST}"
  echo "Then visit: http://127.0.0.1:${NOVNC_PORT}/vnc.html?host=127.0.0.1&port=${NOVNC_PORT}&autoconnect=1"
  exit 0
fi

Xvfb "$DISPLAY_ID" -screen 0 1920x1080x24 -nolisten tcp >"$RUNTIME_DIR/xvfb.log" 2>&1 &
echo $! > "$RUNTIME_DIR/xvfb.pid"
sleep 1

DISPLAY="$DISPLAY_ID" openbox >"$RUNTIME_DIR/openbox.log" 2>&1 &
echo $! > "$RUNTIME_DIR/openbox.pid"

x11vnc -display "$DISPLAY_ID" -localhost -nopw -forever -shared -rfbport "$VNC_PORT" \
  >"$RUNTIME_DIR/x11vnc.log" 2>&1 &
echo $! > "$RUNTIME_DIR/x11vnc.pid"

websockify --web=/usr/share/novnc/ "127.0.0.1:${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}" \
  >"$RUNTIME_DIR/novnc.log" 2>&1 &
echo $! > "$RUNTIME_DIR/novnc.pid"

DISPLAY="$DISPLAY_ID" google-chrome \
  --user-data-dir="$PROFILE_DIR" \
  --profile-directory=Default \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$CDP_PORT" \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  "$URL" >"$RUNTIME_DIR/chrome.log" 2>&1 &
echo $! > "$RUNTIME_DIR/chrome.pid"

chmod 600 "$RUNTIME_DIR"/*.pid "$RUNTIME_DIR"/*.log 2>/dev/null || true

cat <<EOF
Private challenge browser started.

From your other device, open an SSH tunnel:
  ssh -N -L ${NOVNC_PORT}:127.0.0.1:${NOVNC_PORT} barbary@${REMOTE_HOST}

Then open this local URL on that device:
  http://127.0.0.1:${NOVNC_PORT}/vnc.html?host=127.0.0.1&port=${NOVNC_PORT}&autoconnect=1

This is bound to 127.0.0.1 on the remote and is not public.
The worker was paused while the shared browser profile is open.
When done, run:
  $ROOT/scripts/stop_challenge_browser.sh

Logs:
  $RUNTIME_DIR/
EOF
