#!/usr/bin/env bash
set -euo pipefail

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  DOCKER=(sudo -S docker)
fi

"${DOCKER[@]}" rm -f job-agent-searxng >/dev/null 2>&1 || true
echo "Local SearXNG stopped."
