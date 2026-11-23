#!/usr/bin/env python3
"""CLI entry for the standup skill — bootstraps ai/lib, then standup.gather."""

from pathlib import Path
import sys

_AI_LIB = Path(__file__).resolve().parents[2] / "lib"
sys.path.insert(0, str(_AI_LIB))

from standup.gather import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
