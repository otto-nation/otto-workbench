#!/usr/bin/env bash
# run_ci.sh's own exit code must be the suite's. Echoing EXIT=$? as the last
# statement reports echo's status instead.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
work_root=$(mktemp -d)
trap 'rm -rf "$work_root"' EXIT

fail() {
  echo "verify: $1" >&2
  exit 1
}

cp "$here/run_ci.sh" "$here/suite.sh" "$work_root/"
cd "$work_root"

SUITE_EXIT=1 bash ./run_ci.sh > wrap.out 2>&1
rc=$?
[[ "$rc" -ne 0 ]] || fail "run_ci.sh exited 0 for a failing suite (status was masked)"

SUITE_EXIT=0 bash ./run_ci.sh > wrap.out 2>&1
rc=$?
[[ "$rc" -eq 0 ]] || fail "run_ci.sh failed for a passing suite"
echo "verify: ok"
