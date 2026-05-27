#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
systemctl --user --no-pager status job-agent-browser.service || true
printf '\n--- worker status ---\n'
test -f "$ROOT/data/applications/worker_status.json" && cat "$ROOT/data/applications/worker_status.json" || true
printf '\n--- worker log tail ---\n'
test -f "$ROOT/data/logs/worker.log" && tail -n 80 "$ROOT/data/logs/worker.log" || true
