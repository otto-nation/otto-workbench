#!/usr/bin/env bash
# A regression test must fail when the inclusive upper bound is broken. Tests
# that only cover a midpoint still pass after that break and prove nothing.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
work_root=$(mktemp -d)
trap 'rm -rf "$work_root"' EXIT

fail() {
  echo "verify: $1" >&2
  exit 1
}

# The copy is what gets broken, never the tracked fixture. Mutating
# $here/bounds.py in place and restoring it from the EXIT trap looked
# equivalent, but an EXIT trap does not run on SIGKILL — which is how a CI
# harness ends a step that hangs or runs over time, and what an OOM kill does.
# A run killed between the write and the trap left the planted exclusive-bound
# bug committed in the working tree, so every later run read a corpus file that
# no longer matched its manifest. Every sibling fixture already works this way.
cp "$here/bounds.py" "$work_root/bounds.py"
cp "$here/test_in_range.py" "$work_root/test_in_range.py"
cd "$work_root" || exit

env -u PYTEST_CURRENT_TEST -u PYTEST_ADDOPTS pytest test_in_range.py > "$work_root/before.txt" 2>&1
rc=$?
[[ "$rc" -eq 0 ]] || fail "tests must pass on the current (fixed) code"

python3 -c '
from pathlib import Path
p = Path("bounds.py")
text = p.read_text()
needle = "return lo <= n <= hi"
if needle not in text:
    raise SystemExit("bounds.py does not contain the inclusive comparison to break")
p.write_text(text.replace(needle, "return lo <= n < hi", 1))
' || fail "could not reintroduce the exclusive-bound bug"

env -u PYTEST_CURRENT_TEST -u PYTEST_ADDOPTS pytest test_in_range.py > "$work_root/after.txt" 2>&1
rc=$?
[[ "$rc" -ne 0 ]] || fail "no regression test: tests still pass after reintroducing the exclusive-bound bug"
echo "verify: ok"
