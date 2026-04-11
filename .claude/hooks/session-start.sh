#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Installs the farmafacil package plus dev extras so tests, linters, and the
# FastAPI app can run inside a remote web session. Idempotent and
# non-interactive — safe to re-run on resume/clear/compact sources.
set -euo pipefail

# Only run in remote (Claude Code on the web) environments. Local sessions
# already have the project installed via `pip install -e ".[dev]"`.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    echo "Not a remote session, skipping dependency install."
    exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

echo "==> Installing farmafacil with dev extras (editable)"
python3 -m pip install --quiet -e ".[dev]"

# Bootstrap a .env file from the example so settings load during tests.
if [ ! -f .env ] && [ -f .env.example ]; then
    echo "==> Seeding .env from .env.example"
    cp .env.example .env
fi

# Ensure the src layout is importable for any tooling that bypasses the
# installed distribution.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    echo "export PYTHONPATH=\"${CLAUDE_PROJECT_DIR:-$(pwd)}/src:\${PYTHONPATH:-}\"" >> "$CLAUDE_ENV_FILE"
fi

echo "==> Session start hook completed successfully"
