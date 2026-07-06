---
name: release
description: Step-by-step guide to bump the version, back up, and rebuild both garmin-coach.mcpb / garmin-coach-kayleigh.mcpb distributable bundles after code changes, then deploy locally.
---

# Release Checklist

Run this after any code change to produce fresh distributable `.mcpb` bundles.

## 1. Bump version in manifest.json

- Patch bump (x.y.**Z**) for bug fixes and minor tweaks
- Minor bump (x.**Y**.0) for new features or new tools
- Check current version: `grep '"version"' manifest.json`

## 2. Back up the current mcpb

```bash
cp garmin-coach.mcpb backups/garmin-coach_pre-<new-version>-<short-desc>_$(date +%Y%m%d).mcpb
```

Prune old backups — keep only the 2 most recent:

```bash
ls -t backups/garmin-coach_pre-*.mcpb | tail -n +3 | xargs rm -f
```

## 3. Rebuild both bundles

Always build both — Kayleigh's bundle gets a patched manifest (`name`/`display_name` only, same code):

```bash
# Kyle's bundle (base manifest: garmin-coach)
zip -r /tmp/garmin-coach-new.mcpb . \
  -x "./backups/*" -x "./.DS_Store" -x "./__pycache__/*" -x "./server/__pycache__/*" \
  -x "./garmin-coach.mcpb" -x "./garmin-coach-kayleigh.mcpb" \
  -x "./.git/*" -x "./.gitignore" -x "./.claude/*" -x "./.venv/*"
cp /tmp/garmin-coach-new.mcpb garmin-coach.mcpb && rm /tmp/garmin-coach-new.mcpb

# Kayleigh's bundle — same code, name/display_name patched in manifest
python3 -c "
import json
m = json.load(open('manifest.json'))
m['name'] = 'garmin-coach-kayleigh'
m['display_name'] = 'Garmin Morning Coach (Kayleigh)'
json.dump(m, open('manifest.json', 'w'), indent=2)
"
zip -r /tmp/garmin-coach-kayleigh-new.mcpb . \
  -x "./backups/*" -x "./.DS_Store" -x "./__pycache__/*" -x "./server/__pycache__/*" \
  -x "./garmin-coach.mcpb" -x "./garmin-coach-kayleigh.mcpb" \
  -x "./.git/*" -x "./.gitignore" -x "./.claude/*" -x "./.venv/*"
cp /tmp/garmin-coach-kayleigh-new.mcpb garmin-coach-kayleigh.mcpb && rm /tmp/garmin-coach-kayleigh-new.mcpb
# Restore manifest
python3 -c "
import json
m = json.load(open('manifest.json'))
m['name'] = 'garmin-coach'
m['display_name'] = 'Garmin Morning Coach'
json.dump(m, open('manifest.json', 'w'), indent=2)
"
```

## 4. Verify

```bash
unzip -l garmin-coach.mcpb | grep <changed-file>
```

Check the file timestamps in the listing match your changes.

## 5. Smoke test (optional but recommended)

```bash
.venv/bin/python3 -c "import sys; sys.path.insert(0, 'server'); import main; print('OK')"
```

## 6. Deploy

```bash
./deploy.sh
```

Syncs both extensions into Claude and restarts the app.
