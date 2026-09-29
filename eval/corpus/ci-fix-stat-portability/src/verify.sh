#!/usr/bin/env bash
# report.sh must print a bare integer size under a BSD stat, not a filesystem
# report. The stub is on PATH so GNU hosts fail the same way BSD would.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
work_root=$(mktemp -d)
trap 'rm -rf "$work_root"' EXIT

fail() {
  echo "verify: $1" >&2
  exit 1
}

fixture="$work_root/sample.txt"
printf 'hello\n' > "$fixture"

out=$(PATH="$here/stubs/bsd:/usr/bin:/bin" bash "$here/report.sh" "$fixture" 2>/dev/null) \
  || fail "report.sh failed"
[[ "$out" =~ ^[0-9]+$ ]] || fail "report.sh stdout is not a bare integer: $out"
# Darwin wc -c < file pads with leading spaces; awk strips them.
expected=$(wc -c < "$fixture" | awk '{print $1}')
[[ "$out" == "$expected" ]] || fail "report.sh size is wrong: got $out want $expected"
echo "verify: ok"
