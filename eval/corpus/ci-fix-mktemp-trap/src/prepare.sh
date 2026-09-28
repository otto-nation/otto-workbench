#!/usr/bin/env bash
# Prepares a scratch directory, writes a marker, then tears down.
set -euo pipefail

dir=$(mktemp -d)
printf 'ready\n' > "$dir/marker"
if [[ "${1:-}" == "--fail" ]]; then
  echo "error: simulated failure" >&2
  exit 1
fi
cat "$dir/marker"
rm -rf "$dir"
