#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Python virtualenv is missing. Run scripts/install.sh first."
  exit 1
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export JOB_AGENT_DASHBOARD_HOST="${JOB_AGENT_DASHBOARD_HOST:-127.0.0.1}"
export JOB_AGENT_DASHBOARD_PORT="${JOB_AGENT_DASHBOARD_PORT:-7860}"

if [[ "${JOB_AGENT_WEB_SEARCH_PROVIDER:-searxng}" == "searxng" ]]; then
  if command -v docker >/dev/null 2>&1; then
    echo "Starting local-only SearXNG search helper if needed..."
    scripts/start_local_search.sh || echo "Local search did not start. You can still use the dashboard; see the Web Search tab."
  else
    echo "Docker is not available, so the local search helper is skipped."
  fi
fi

echo "Starting dashboard on http://${JOB_AGENT_DASHBOARD_HOST}:${JOB_AGENT_DASHBOARD_PORT}"
echo "Press Ctrl+C to stop this foreground dashboard."
exec .venv/bin/python -m app.main dashboard --host "$JOB_AGENT_DASHBOARD_HOST" --port "$JOB_AGENT_DASHBOARD_PORT"
