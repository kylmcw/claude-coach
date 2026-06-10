---
name: release
description: Step-by-step guide to bump the version, back up, and rebuild the garmin-coach.mcpb distributable bundle after code changes.
---

# Release Checklist

Run this after any code change to produce a new distributable `garmin-coach.mcpb`.

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

## 3. Rebuild the bundle

```bash
zip -r /tmp/garmin-coach-new.mcpb . \
  -x "./backups/*" \
  -x "./.DS_Store" \
  -x "./__pycache__/*" \
  -x "./server/__pycache__/*" \
  -x "./garmin-coach.mcpb" \
  -x "./.git/*" \
  -x "./.gitignore" \
  -x "./.claude/*" \
  -x "./.venv/*"
cp /tmp/garmin-coach-new.mcpb garmin-coach.mcpb
rm /tmp/garmin-coach-new.mcpb
```

## 4. Verify

```bash
unzip -l garmin-coach.mcpb | grep server/
unzip -l garmin-coach.mcpb | grep manifest.json
```

Check the file timestamps in the listing match your changes.

## 5. Smoke test (optional but recommended)

```bash
.venv/bin/python3 -c "import sys; sys.path.insert(0, 'server'); import main; print('OK')"
```
