#!/usr/bin/env bash
# Prints the byte size of the file given as $1.
set -euo pipefail

f="$1"
# wc -c on stdin is the portable size read; both BSD and GNU stat format
# flags are userland-specific.
wc -c < "$f" | awk '{print $1}'
