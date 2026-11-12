#!/usr/bin/env bash
# Run the rsvp CLI on macOS:  ./rsvp.sh <command> [args]
# Keeps Python, the virtualenv and the uv cache outside OneDrive,
# so they don't sync between machines.
set -euo pipefail
base="$HOME/.rsvp-reader"
export UV_PYTHON_INSTALL_DIR="$base/python"
export UV_PROJECT_ENVIRONMENT="$base/venv-macos"
export UV_CACHE_DIR="$base/uv-cache"
export UV_PYTHON_PREFERENCE=only-managed
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "test" ]]; then
  shift
  exec uv run --project "$here" pytest "$@"   # ./rsvp.sh test [pytest args]
fi
exec uv run --project "$here" rsvp "$@"
