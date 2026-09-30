#!/usr/bin/env bats
# Tests for the secret scan in git/hooks/pre-push-workbench.
#
# The sibling suite pre_commit_gitleaks.bats covers the same 0/7/other contract
# in git/hooks/pre-commit. This one exists because the two hooks spell it
# differently and the difference is load-bearing: pre-push-workbench traps ERR,
# so a bare `out=$(cmd)` assignment fires that trap even under `set +e` and
# prints "✗ pre-push hook failed (exit 7)" before the real message — a working
# gate that reads as a crashed one. That was introduced and caught by hand once;
# without a case, the next edit back to the bare form regresses silently.
#
# The hook is run directly rather than through a push. It resolves WORKBENCH_DIR
# from `git rev-parse --show-toplevel` and sources this checkout's lib/, so it
# only runs inside a workbench worktree — a throwaway repo cannot host it. The
# scan sits near the top, before the expensive validators, so a stubbed gitleaks
# that fails ends the run there and the cases stay fast.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  BIN="$TMPDIR/bin"
  mkdir -p "$BIN"
}

teardown() {
  common_teardown
}

# _stub_gitleaks MODE [STDERR_TEXT] — a fake gitleaks on PATH.
#
# MODE is "leaks" or a literal exit code. "leaks" honours --exit-code the way
# the real binary does, falling back to gitleaks' default of 1 when the hook
# passed no flag — so a hook that drops the flag, or sets it back to 1, is
# caught here rather than passing on a stub that always agreed with it.
_stub_gitleaks() {
  local mode="$1" msg="${2:-}"
  cat > "$BIN/gitleaks" << SCRIPT
#!/usr/bin/env bash
leak_code=1
prev=""
for arg in "\$@"; do
  [[ "\$prev" == "--exit-code" ]] && leak_code="\$arg"
  prev="\$arg"
done
[[ -n "$msg" ]] && echo "$msg" >&2
if [[ "$mode" == leaks ]]; then
  echo "secret detected in staged.txt"
  exit "\$leak_code"
fi
exit $mode
SCRIPT
  chmod +x "$BIN/gitleaks"
}

# _run_hook — the real pre-push hook, with the stub ahead of any real gitleaks.
_run_hook() {
  PATH="$BIN:$PATH" run "$REPO_ROOT/git/hooks/pre-push-workbench"
}

@test "a leak is reported as a leak, with the findings" {
  _stub_gitleaks leaks
  _run_hook
  [ "$status" -ne 0 ]
  [[ "$output" == *"secret detected in staged.txt"* ]]
  [[ "$output" != *"NOT a secret finding"* ]]
}

@test "a leak does not print a hook-crash line" {
  # The regression this branch fixes. With the bare-assignment form the ERR
  # trap fires first and the operator sees "pre-push hook failed (exit 7)" —
  # a gate doing its job, reported as a gate that broke.
  _stub_gitleaks leaks
  _run_hook
  [ "$status" -ne 0 ]
  [[ "$output" != *"pre-push hook failed"* ]]
}

@test "a gitleaks that cannot run is not called a secret finding" {
  _stub_gitleaks 1 "mise ERROR error parsing config file"
  _run_hook
  [ "$status" -ne 0 ]
  [[ "$output" == *"could not run"* ]]
  [[ "$output" == *"NOT a secret finding"* ]]
}

@test "a scanner failure does not print a hook-crash line either" {
  _stub_gitleaks 1 "mise ERROR error parsing config file"
  _run_hook
  [ "$status" -ne 0 ]
  [[ "$output" != *"pre-push hook failed"* ]]
}

@test "the scanner's own words survive into the failure report" {
  # Whatever gitleaks said about why it could not start is what diagnoses it.
  _stub_gitleaks 1 "TOML parse error at line 1, column 6"
  _run_hook
  [ "$status" -ne 0 ]
  [[ "$output" == *"TOML parse error"* ]]
}

@test "an unexpected exit code is a scanner failure, not a leak" {
  _stub_gitleaks 127
  _run_hook
  [ "$status" -ne 0 ]
  [[ "$output" == *"could not run"* ]]
  [[ "$output" != *"secret detected"* ]]
}
