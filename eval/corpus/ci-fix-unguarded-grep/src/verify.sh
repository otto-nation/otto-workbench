#!/usr/bin/env bash
# scan.sh must treat zero matches as a count of 0, not as a script failure.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
work_root=$(mktemp -d)
trap 'rm -rf "$work_root"' EXIT

fail() {
  echo "verify: $1" >&2
  exit 1
}

printf 'hello\n' > "$work_root/clean.txt"
if ! bash "$here/scan.sh" "$work_root/clean.txt" > "$work_root/out"; then
  fail "scan.sh must accept a file with no matches"
fi
[[ "$(cat "$work_root/out")" == "0" ]] || fail "expected count 0, got $(cat "$work_root/out")"

printf 'TODO: x\nfoo TODO\n' > "$work_root/hits.txt"
if ! bash "$here/scan.sh" "$work_root/hits.txt" > "$work_root/out"; then
  fail "scan.sh failed on a file with matches"
fi
[[ "$(cat "$work_root/out")" == "2" ]] || fail "expected count 2, got $(cat "$work_root/out")"
echo "verify: ok"
