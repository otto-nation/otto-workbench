#!/usr/bin/env bats
# The ShellCheck the pre-push gate runs and the one CI runs must be the same
# version, or a green gate is not a prediction about CI.
#
# What prompted this: the gate passed a branch whose bats file used `$stderr`
# after `run --separate-stderr`, and CI failed it on SC2154. ubuntu-24.04 ships
# 0.9, which does not know bats assigns that variable; brew had 0.11, which
# does. The finding was real for 0.9 and unreproducible locally, and it cost a
# round trip on an already-merged PR to discover why.
#
# The pin lives in .github/actions/install-shellcheck/action.yml. Brew does not
# pin, so this compares the pin against whatever is actually on PATH — which is
# the brew build locally and the pinned build in CI. Either one drifting from
# the pin fails here.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  ACTION="$REPO_ROOT/.github/actions/install-shellcheck/action.yml"
}

teardown() {
  common_teardown
}

# _pinned — the version the CI action installs, as `v0.11.0`.
_pinned() {
  grep -E '^\s+SHELLCHECK_VERSION:' "$ACTION" | awk '{print $2}'
}

@test "the CI action pins a concrete version" {
  local pinned
  pinned="$(_pinned)"
  [[ -n "$pinned" ]]
  # A tag, not a branch or `latest`: the point of the pin is that the version
  # moves when a person moves it.
  [[ "$pinned" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

@test "the shellcheck on PATH is the version CI pins" {
  # Skipped rather than failed where the tool is absent: the gate that runs
  # shellcheck already refuses to proceed without it, and this suite should not
  # be the thing that reports a missing dependency.
  command -v shellcheck >/dev/null 2>&1 || skip "shellcheck not installed"

  local pinned installed
  pinned="$(_pinned)"
  installed="v$(shellcheck --version | awk '/^version:/ {print $2}')"

  [[ "$installed" == "$pinned" ]] || {
    echo "shellcheck on PATH is $installed, CI pins $pinned" >&2
    echo "Bump SHELLCHECK_VERSION in $ACTION and fix whatever the new" >&2
    echo "version finds, or pin brew back. A gate on one version cannot" >&2
    echo "predict a CI job on another." >&2
    return 1
  }
}

@test "the CI job installs shellcheck before running it" {
  # The pin is inert if the job never uses the action: the runner's own 0.9
  # stays on PATH and the two diverge again silently.
  local workflow="$REPO_ROOT/.github/workflows/ci.yml"
  # From the job's own key to the next one at the same indent. NR>1 on the end
  # pattern is what stops `shellcheck:` terminating its own range.
  local job
  job="$(awk '/^  shellcheck:/{f=1} f && /^  [a-z_-]+:$/ && ++n>1{exit} f' "$workflow")"

  [[ "$job" == *"uses: ./.github/actions/install-shellcheck"* ]]
  # And the install has to come before the lint step that depends on it.
  local install_at lint_at
  install_at="$(printf '%s\n' "$job" | grep -n "install-shellcheck" | head -1 | cut -d: -f1)"
  lint_at="$(printf '%s\n' "$job" | grep -n "list_shell_scripts" | head -1 | cut -d: -f1)"
  [[ -n "$install_at" && -n "$lint_at" ]]
  [[ "$install_at" -lt "$lint_at" ]]
}
