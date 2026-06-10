# Server Architecture

<!-- Loaded lazily when Claude reads any file in server/ -->

## Server Setup

- MCP server: `mcp.server.Server` with stdio transport (`mcp.server.stdio.stdio_server`)
- Entry point: `app = Server("garmin-coach")` in `main.py`
- Started via `start.sh` → `.venv/bin/python3 server/main.py`

## Auth & Client

- Credentials from env vars `GARMIN_EMAIL` / `GARMIN_PASSWORD` (set by manifest user_config)
- Lazy singleton: `get_client()` in `client.py` creates `Garmin(email, pw)` + `.login()` on first call
- Client is reused across all tool calls in a session

## Tool Registration Pattern

- `@app.list_tools()` → delegates to `get_tool_definitions()` in `tools.py`
- `@app.call_tool()` → single async dispatcher using `if/elif` on tool name
- Each branch calls domain module functions, returns `[types.TextContent(...)]`
- No per-tool decorators — everything routes through the one dispatcher in `main.py`

## Data Flow

1. `fetch_*()` functions call `garminconnect` library (sync/blocking)
2. Scoring functions (e.g. `assess_readiness()`) interpret raw data
3. `format_*()` functions build the output string
4. Dispatcher wraps in `TextContent` and returns

## Standard Imports

```python
import asyncio, json, os
import urllib.parse, urllib.request
from datetime import date, timedelta
from pathlib import Path
from garminconnect import Garmin
from garminconnect.workout import (
    BaseWorkout, RunningWorkout, FitnessEquipmentWorkout,
    WorkoutSegment, ExecutableStep, RepeatGroup, StepType,
    ConditionType, TargetType, create_warmup_step,
    create_cooldown_step, create_interval_step,
    create_recovery_step, create_repeat_group,
)
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
```

## Dependencies

- `garminconnect` — Garmin Connect API wrapper (sync)
- `mcp` — MCP server framework
- `urllib.request` / `urllib.parse` — stdlib only; no requests/httpx/aiohttp
