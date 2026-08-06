#!/usr/bin/env bash
# build.sh must stamp the version under both sed dialects. Each check runs on a
# fresh copy, so the order of the checks cannot change the outcome.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
work_root=$(mktemp -d)
trap 'rm -rf "$work_root"' EXIT

fail() {
  echo "verify: $1" >&2
  exit 1
}

check() {
  local label="$1" path="$2"
  local work="$work_root/$label"
  mkdir -p "$work"
  cp "$here/version.txt" "$work/version.txt"
  PATH="$path" bash "$here/build.sh" "$work/version.txt" >/dev/null 2>&1 \
    || fail "build.sh failed under $label sed"
  grep -q "VERSION=1.0.0" "$work/version.txt" \
    || fail "build.sh did not stamp the version under $label sed"
}

check strict "$here/stubs/strict:/usr/bin:/bin"
check system "/usr/bin:/bin"
echo "verify: ok"
