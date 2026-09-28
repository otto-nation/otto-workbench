#!/usr/bin/env bash
# prepare.sh must remove its scratch directory on every exit, including the
# failure path. A trailing rm is skipped by exactly the exits that leak.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
work_root=$(mktemp -d)
trap 'rm -rf "$work_root"' EXIT

fail() {
  echo "verify: $1" >&2
  exit 1
}

export MKTEMP_LOG="$work_root/created"
: > "$MKTEMP_LOG"
PATH="$here/stubs/record:/usr/bin:/bin"

out=$(bash "$here/prepare.sh") || fail "prepare.sh failed on the success path"
[[ "$out" == "ready" ]] || fail "prepare.sh did not print the marker on success"
dir=$(cat "$MKTEMP_LOG")
[[ -n "$dir" ]] || fail "prepare.sh did not create a temp dir"
[[ ! -d "$dir" ]] || fail "temp dir leaked on the success path: $dir"

: > "$MKTEMP_LOG"
bash "$here/prepare.sh" --fail >/dev/null 2>&1
dir=$(cat "$MKTEMP_LOG")
[[ -n "$dir" ]] || fail "prepare.sh did not create a temp dir on the failure path"
[[ ! -d "$dir" ]] || fail "temp dir leaked after early exit: $dir"
echo "verify: ok"
