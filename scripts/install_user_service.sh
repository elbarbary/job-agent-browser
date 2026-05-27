#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/job-agent-browser.service"
mkdir -p "$SERVICE_DIR" "$ROOT/data/logs/runtime"
chmod 700 "$SERVICE_DIR" "$ROOT/data/logs/runtime"

cat > "$SERVICE_FILE" <<UNIT
[Unit]
Description=Local-first job application browser worker
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=ANONYMIZED_TELEMETRY=false
ExecStart=$ROOT/.venv/bin/python -m app.main worker
Restart=always
RestartSec=30
NoNewPrivileges=true

[Install]
WantedBy=default.target
UNIT

chmod 600 "$SERVICE_FILE"
systemctl --user daemon-reload
systemctl --user enable --now job-agent-browser.service

if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$USER" 2>/dev/null || {
    printf 'Warning: could not enable linger. The user service is started now, but may stop after logout unless linger is enabled by an admin.\n' >&2
  }
fi

systemctl --user --no-pager status job-agent-browser.service
