#!/usr/bin/env bash
# Runs the project smoke test through uv.
set -euo pipefail

uv run --quiet smoke
