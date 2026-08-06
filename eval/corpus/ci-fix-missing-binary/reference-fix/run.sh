#!/usr/bin/env bash
# Runs the project smoke test through uv.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed — install it to run the smoke test" >&2
  exit 1
fi

uv run --quiet smoke
