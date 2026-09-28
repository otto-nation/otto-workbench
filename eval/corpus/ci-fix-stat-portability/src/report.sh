#!/usr/bin/env bash
# Prints the byte size of the file given as $1.
set -euo pipefail

f="$1"
stat -c %s "$f"
