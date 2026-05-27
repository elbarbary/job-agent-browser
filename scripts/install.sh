#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
umask 077

chmod 700 "$ROOT"
mkdir -p data/cv data/profiles data/sessions/browser-profile data/logs/runs \
  data/logs/screenshots data/applications/drafts data/applications/approvals
chmod -R go-rwx data

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --index-url https://pypi.org/simple -r requirements.txt

# Browser Use operates locally through its Browser actor; keep the browser runtime local.
.venv/bin/python -m playwright install chromium

printf 'Installation complete. Run: scripts/run_smoke_test.sh\n'
