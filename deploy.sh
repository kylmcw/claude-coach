#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTENSIONS_DIR="$HOME/Library/Application Support/Claude/Claude Extensions"
KYLE_DIR="$EXTENSIONS_DIR/local.mcpb.you.garmin-coach"
KAYLEIGH_DIR="$EXTENSIONS_DIR/local.mcpb.you.garmin-coach-kayleigh"

EXCLUDE=(
  --exclude=".git/"
  --exclude=".claude/"
  --exclude=".venv/"
  --exclude="__pycache__/"
  --exclude="*.mcpb"
  --exclude="backups/"
  --exclude=".DS_Store"
)

echo "Deploying garmin-coach..."
rsync -a --delete "${EXCLUDE[@]}" "$SCRIPT_DIR/" "$KYLE_DIR/"

echo "Deploying garmin-coach-kayleigh..."
rsync -a --delete "${EXCLUDE[@]}" "$SCRIPT_DIR/" "$KAYLEIGH_DIR/"
python3 -c "
import json, pathlib
p = pathlib.Path('$KAYLEIGH_DIR/manifest.json')
m = json.loads(p.read_text())
m['name'] = 'garmin-coach-kayleigh'
m['display_name'] = 'Garmin Morning Coach (Kayleigh)'
p.write_text(json.dumps(m, indent=2))
"

echo "Restarting Claude..."
killall Claude 2>/dev/null || true
sleep 1
open /Applications/Claude.app

echo "Done."
