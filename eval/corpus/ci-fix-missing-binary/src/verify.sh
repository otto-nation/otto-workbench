#!/usr/bin/env bash
# run.sh must work when uv is installed, and when it is not it must fail with a
# diagnostic that names uv. A bare 127 from the shell tells nobody what is
# missing, which is exactly what made the original failure expensive to read.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)

fail() {
  echo "verify: $1" >&2
  exit 1
}

PATH="$here/stubs/present:/usr/bin:/bin" bash "$here/run.sh" >/dev/null 2>&1 \
  || fail "run.sh must succeed when uv is on PATH"

out=$(PATH="/usr/bin:/bin" bash "$here/run.sh" 2>&1)
rc=$?
[ "$rc" -ne 0 ] || fail "run.sh must fail when uv is missing"
[ "$rc" -ne 127 ] || fail "run.sh exited 127 — detect the missing tool instead"
printf '%s' "$out" | grep -qi "uv" || fail "the error must name uv"
echo "verify: ok"
