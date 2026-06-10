#!/usr/bin/env python3
"""
Post-edit hook: runs the post-edit-reviewer agent on changed project files.
Invoked by Claude Code after every Edit or Write tool call.
Receives tool context via stdin as JSON.
"""
import sys
import json
import subprocess
import os

# Resolve project root from the env var Claude Code sets for hooks; fall back to
# this file's location (PROJECT/.claude/hooks/post_edit_review.py) so a rename can't break it.
PROJECT_DIR = os.environ.get(
    "CLAUDE_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

# Files that warrant a full code review
REVIEWABLE_EXTENSIONS = {".py", ".sh"}
REVIEWABLE_NAMES = {"manifest.json", "requirements.txt"}


def is_reviewable(file_path: str) -> bool:
    if not file_path:
        return False
    _, ext = os.path.splitext(file_path)
    name = os.path.basename(file_path)
    return ext in REVIEWABLE_EXTENSIONS or name in REVIEWABLE_NAMES


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    file_path = data.get("tool_input", {}).get("file_path", "")

    if not is_reviewable(file_path):
        sys.exit(0)

    # Fast syntax check for Python files — fail immediately before full review
    if file_path.endswith(".py"):
        check = subprocess.run(
            ["python3", "-m", "py_compile", file_path],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            print(f"⛔ SYNTAX ERROR in {file_path} — fix before proceeding:\n{check.stderr}", flush=True)
            sys.exit(1)  # Non-zero exit surfaces this as a blocking problem in Claude Code

    # Invoke the post-edit-reviewer agent
    result = subprocess.run(
        [
            "claude",
            "--agent", "post-edit-reviewer",
            "--print",
            "--dangerously-skip-permissions",
            "--add-dir", PROJECT_DIR,
            "-p", (
                f"A code change was just made to {file_path}. "
                "Run your full review sequence on this file as defined in your instructions. "
                "If you find P1 or P2 issues, list them clearly so the senior-engineer agent can act on them."
            ),
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
    )

    if result.stdout:
        print(result.stdout, flush=True)
    if result.returncode != 0 and result.stderr:
        print(f"[post-edit-reviewer error]: {result.stderr}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
