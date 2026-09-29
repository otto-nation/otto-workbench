#!/usr/bin/env bash
# Counts TODO markers in the file given as $1. Zero matches is a valid count.
set -euo pipefail

count=$(grep -c "TODO" "$1")
printf '%s\n' "$count"
