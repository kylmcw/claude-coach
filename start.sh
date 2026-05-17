#!/bin/bash
# Self-healing startup script for garmin-coach MCP server.
# Works even if the venv's python3.14 symlink is broken (e.g. after a Homebrew update).
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"
PYTHON="$VENV/bin/python3"

# Fast path: venv python works fine
if "$PYTHON" -c "import sys" 2>/dev/null; then
    exec "$PYTHON" "$DIR/server/main.py"
fi

# Venv python is broken — find python3.14 from common Homebrew locations or PATH
SYSTEM_PY=""
for candidate in \
    /opt/homebrew/opt/python@3.14/bin/python3.14 \
    /opt/homebrew/bin/python3.14 \
    /usr/local/opt/python@3.14/bin/python3.14 \
    /usr/local/bin/python3.14; do
    if [ -x "$candidate" ]; then
        SYSTEM_PY="$candidate"
        break
    fi
done

# Fall back to PATH
if [ -z "$SYSTEM_PY" ]; then
    SYSTEM_PY=$(command -v python3.14 2>/dev/null || command -v python3 2>/dev/null || true)
fi

if [ -z "$SYSTEM_PY" ]; then
    echo "garmin-coach: ERROR — could not find python3.14 or python3" >&2
    exit 1
fi

echo "garmin-coach: relinking venv to $SYSTEM_PY" >&2

# --upgrade repoints the interpreter without wiping installed packages.
# If that fails (major version mismatch), fall back to a clean rebuild.
if ! "$SYSTEM_PY" -m venv "$VENV" --upgrade 2>/dev/null; then
    "$SYSTEM_PY" -m venv "$VENV" --clear
    "$VENV/bin/pip" install -q garminconnect mcp
fi

exec "$VENV/bin/python3" "$DIR/server/main.py"
