#!/usr/bin/env bats
# The pre-push hook's pytest selection: _select_pytest is sourced out of the
# workbench hook and run against a stub select-pytest, so the budget ladder —
# transitive under the cap, --direct over it, skip when still over — is told
# apart without running a suite.
# shellcheck disable=SC2154  # _pytest_* are assigned by the sourced function

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup

  HOOK="$REPO_ROOT/git/hooks/pre-push-workbench"

  FIXTURE="$TMPDIR/wb"
  mkdir -p "$FIXTURE/bin/local"
  export REPO_ROOT="$FIXTURE"
  export GIT_REMOTE=origin
  # shellcheck disable=SC2034  # read by the sourced _select_pytest
  _default_branch=main

  local src
  src="$(sed -n '/^PREPUSH_PYTEST_FILE_CAP=/,/^}/p' "$HOOK")"
  [[ -n "$src" ]] || { echo "PREPUSH_PYTEST_FILE_CAP/_select_pytest not found in $HOOK" >&2; return 1; }
  eval "$src"
  [[ -n "${PREPUSH_PYTEST_FILE_CAP:-}" ]] || { echo "cap did not eval" >&2; return 1; }
}

teardown() {
  common_teardown
}

# _stub_selector — select-pytest driven by TRANSITIVE_N / DIRECT_N / ALL_N.
_stub_selector() {
  cat > "$FIXTURE/bin/local/select-pytest" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$REPO_ROOT/calls"
[[ "${SELECTOR_FAIL:-}" == "1" ]] && exit 1
n="${TRANSITIVE_N:-10}"
case " $* " in
  *" --all "*) n="${ALL_N:-100}" ;;
  *" --direct "*) n="${DIRECT_N:-2}" ;;
esac
[[ "${EMPTY:-}" == "1" && " $* " != *" --all "* ]] && exit 0
i=1
while (( i <= n )); do
  echo "t${i}.py"
  i=$((i + 1))
done
EOF
  chmod +x "$FIXTURE/bin/local/select-pytest"
}

@test "a set under the cap is used as-is and does not ask for --direct" {
  _stub_selector
  TRANSITIVE_N=10 ALL_N=100 _select_pytest
  [ "$_pytest_none" = false ]
  [ "$(echo "$_pytest_selected" | wc -l | tr -d ' ')" -eq 10 ]
  [ "$_pytest_label" = "pytest (10/100 files)" ]
  if grep -q -- '--direct' "$FIXTURE/calls"; then
    echo "under-cap selection must not call --direct" >&2
    return 1
  fi
}

@test "a set over the cap retries with --direct and uses that when it fits" {
  _stub_selector
  TRANSITIVE_N=$((PREPUSH_PYTEST_FILE_CAP + 10)) DIRECT_N=2 ALL_N=100 \
    _select_pytest
  [ "$_pytest_none" = false ]
  [ "$(echo "$_pytest_selected" | wc -l | tr -d ' ')" -eq 2 ]
  [[ "$_pytest_label" == *"2/100 files, direct"* ]]
  grep -q -- '--direct' "$FIXTURE/calls"
}

@test "a set still over the cap after --direct is skipped for CI" {
  _stub_selector
  local over=$((PREPUSH_PYTEST_FILE_CAP + 10))
  TRANSITIVE_N=$over DIRECT_N=$over ALL_N=100 _select_pytest
  [ "$_pytest_none" = true ]
  [ -z "$_pytest_selected" ]
  [[ "$_pytest_skip_detail" == *"over cap of ${PREPUSH_PYTEST_FILE_CAP}"* ]]
  [[ "$_pytest_skip_detail" == *"CI"* ]]
}

@test "a selector that succeeds and selects nothing marks the step as none affected" {
  _stub_selector
  EMPTY=1 _select_pytest
  [ "$_pytest_none" = true ]
  [ -z "$_pytest_selected" ]
  [ "$_pytest_skip_detail" = "none affected by this diff" ]
}

@test "a selector that fails falls back to the whole suite, not to none" {
  _stub_selector
  SELECTOR_FAIL=1 _select_pytest
  [ "$_pytest_none" = false ]
  [ -z "$_pytest_selected" ]
  [ "$_pytest_label" = "pytest" ]
}

@test "the selector is handed the remote default branch as its base" {
  _stub_selector
  TRANSITIVE_N=1 ALL_N=100 _select_pytest
  grep -q -- '--base origin/main' "$FIXTURE/calls"
}
