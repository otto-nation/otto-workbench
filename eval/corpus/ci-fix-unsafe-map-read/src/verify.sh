#!/usr/bin/env bash
# Missing keys in external JSON must be handled, not raised as KeyError.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
work_root=$(mktemp -d)
trap 'rm -rf "$work_root"' EXIT

fail() {
  echo "verify: $1" >&2
  exit 1
}

printf '%s\n' '{"name": "widget"}' > "$work_root/ok.json"
python3 "$here/read_config.py" "$work_root/ok.json" > "$work_root/out" 2> "$work_root/err"
rc=$?
[[ "$rc" -eq 0 ]] || fail "read_config.py failed on a payload that has name"
[[ "$(cat "$work_root/out")" == "widget" ]] || fail "expected stdout 'widget'"

printf '%s\n' '{"other": 1}' > "$work_root/missing.json"
python3 "$here/read_config.py" "$work_root/missing.json" > "$work_root/out" 2> "$work_root/err"
rc=$?
if grep -q "KeyError" "$work_root/err"; then
  fail "raised KeyError on a missing key — handle the missing case"
fi
[[ "$rc" -ne 0 ]] || fail "must fail when name is missing"
grep -qi "name" "$work_root/err" || fail "the error must name the missing key"
echo "verify: ok"
