#!/usr/bin/env bats
# Tests for ci-failures-guard: refuse gh_ci_failures and drop it from the active set.
# select-on: ai/pi/extensions/* ai/pi/settings.json
setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

_is_ci_failures_tool() {
  run node --input-type=module -e "
    const { isCiFailuresTool } = await import('$REPO_ROOT/ai/pi/extensions/ci-failures-guard/detect.ts');
    process.stdout.write(String(isCiFailuresTool(process.argv[1])));
  " -- "$1"
}

@test "ci-failures-guard: gh_ci_failures is the blocked tool" {
  _is_ci_failures_tool gh_ci_failures
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "ci-failures-guard: neighbouring GitHub tools and bash are not blocked" {
  local name
  for name in gh_merge_queue_failures gh_pr_diff gh_pr_context bash toString; do
    _is_ci_failures_tool "$name"
    [ "$status" -eq 0 ]
    [ "$output" = false ] || {
      echo "blocked a tool that should stay: $name"
      return 1
    }
  done
}

@test "ci-failures-guard: withoutCiFailures drops only gh_ci_failures" {
  run node --input-type=module -e "
    const { withoutCiFailures } = await import('$REPO_ROOT/ai/pi/extensions/ci-failures-guard/detect.ts');
    const dropped = withoutCiFailures(['bash', 'gh_ci_failures', 'gh_pr_diff', 'gh_ci_failures']).join(',');
    const untouched = withoutCiFailures(['bash', 'gh_pr_diff']).join(',');
    process.stdout.write(dropped + '|' + untouched);
  "
  [ "$status" -eq 0 ]
  [ "$output" = "bash,gh_pr_diff|bash,gh_pr_diff" ]
}

@test "ci-failures-guard: the refusal names pr ci and compacted sessions" {
  run node --input-type=module -e "
    const { BLOCK_REASON } = await import('$REPO_ROOT/ai/pi/extensions/ci-failures-guard/detect.ts');
    process.stdout.write(BLOCK_REASON);
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *'pr ci'* ]]
  [[ "$output" == *'compacted sessions'* ]]
}

@test "ci-failures-guard: the extension blocks the tool by name" {
  grep -q 'isCiFailuresTool(event.toolName)' \
    "$REPO_ROOT/ai/pi/extensions/ci-failures-guard/index.ts"
  grep -q 'withoutCiFailures(active)' \
    "$REPO_ROOT/ai/pi/extensions/ci-failures-guard/index.ts"
  grep -q 'pi.setActiveTools(next)' \
    "$REPO_ROOT/ai/pi/extensions/ci-failures-guard/index.ts"
}

@test "ci-failures-guard: the shipped template drops gh_ci_failures from the default tools" {
  # A +/- entry changes Pi's inherited selection rather than replacing it, so
  # the built-in defaults stay and only this tool is removed. Sync copies the
  # key onto the live file as a workbench-managed scalar.
  run jq -c '.defaultTools' "$REPO_ROOT/ai/pi/settings.json"
  [ "$status" -eq 0 ]
  [ "$output" = '["-gh_ci_failures"]' ]
}

@test "ci-failures-guard: detect.ts imports no SDK" {
  [ -f "$REPO_ROOT/ai/pi/extensions/ci-failures-guard/detect.ts" ]
  run grep -nE '^\s*import .*(@earendil-works/pi-coding-agent|isToolCallEventType)' \
    "$REPO_ROOT/ai/pi/extensions/ci-failures-guard/detect.ts"
  [ "$status" -ne 0 ]
}
