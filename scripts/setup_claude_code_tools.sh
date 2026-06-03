#!/usr/bin/env bash
set -euo pipefail

cd "${HOME}"

if [ ! -d "${HOME}/claude-code-official/.git" ]; then
  git clone --depth 1 https://github.com/anthropics/claude-code.git "${HOME}/claude-code-official"
else
  git -C "${HOME}/claude-code-official" pull --ff-only
fi

echo "Official Claude Code repo is available at: ${HOME}/claude-code-official"
echo
echo "Claude Code itself is installed separately by Anthropic's installer."
echo "Recommended install command from the official README:"
echo "  curl -fsSL https://claude.ai/install.sh | bash"
echo
echo "Do not commit Claude auth files, API keys, CVs, sessions, logs, or application data."
