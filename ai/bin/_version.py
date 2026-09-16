"""Shared version helper for claude scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# ai/lib, from WORKBENCH_AI_LIB_DIR's checkout when that is set — see ai/bin/_libdir.py
_AI_LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if os.environ.get("WORKBENCH_AI_LIB_DIR"):
    sys.path.insert(0, str(_AI_LIB_DIR.parent / "bin"))
    from _libdir import pinned_ai_lib_dir
    del sys.path[0]
    _AI_LIB_DIR = pinned_ai_lib_dir()
sys.path.insert(0, str(_AI_LIB_DIR))

from core import timeouts  # noqa: E402

WORKBENCH_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_PATH = WORKBENCH_ROOT / ".github" / ".release-please-manifest.json"


def version_string(name: str) -> str:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {}
    tool_ver = manifest.get("ai/claude", "unknown")
    wb_ver = manifest.get(".", "unknown")
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(WORKBENCH_ROOT), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True, timeout=timeouts.LOCAL,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        sha = "unknown"
    return f"{name} {tool_ver}\notto-workbench {wb_ver} ({sha})"
