#!/usr/bin/env bats
# Tests for oneshot-publish-guard: refuse GitHub writes and Linear filings in json/print sessions.
# select-on: ai/pi/extensions/*
setup() {
  load 'test_helper'
  common_setup

  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_SYNC=true
  mkdir -p "$HOME"

  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# _publishes COMMAND — prints true or false for isPublishing(COMMAND).
_publishes() {
  run node --input-type=module -e "
    const { isPublishing } = await import('$REPO_ROOT/ai/pi/extensions/oneshot-publish-guard/detect.ts');
    process.stdout.write(String(isPublishing(process.argv[1])));
  " -- "$1"
}

@test "oneshot-publish-guard: filing and PR writes match" {
  local cmd
  for cmd in \
    'gh issue create --title x --body y --repo nobody/nothing' \
    'linear issue create --team KEY --title x' \
    'gh pr edit 1 --body x' \
    'gh pr comment 1 --body x' \
    'gh pr review 1 --comment x' \
    'gh api -X POST /repos/o/r/issues' \
    'gh api --method PATCH /repos/o/r/pulls/1' \
    'gh api --method=PUT /repos/o/r/issues/1' \
    'gh api -X DELETE /repos/o/r/issues/1/lock' \
    'pr comments --post' \
    'pr comments --finish --post' \
    'pr describe --post' \
    'pr create --draft' \
    'gh pr merge 1' \
    'gh issue close 5' \
    'gh api -X POST /repos/o/r/git/refs' \
    'gh api graphql -f query=mutation{x}' \
    'pr review 123'; do
    _publishes "$cmd"
    [ "$status" -eq 0 ]
    [ "$output" = true ] || {
      echo "missed a publishing command: $cmd"
      return 1
    }
  done
}

@test "oneshot-publish-guard: reads, GET, triage, and quoted mentions do not match" {
  local cmd
  for cmd in \
    'gh issue list' \
    'gh pr view 1' \
    'gh api /repos/o/r/issues' \
    'gh api -X GET /repos/o/r/issues' \
    'pr comments --triage' \
    "echo 'gh issue create'"; do
    _publishes "$cmd"
    [ "$status" -eq 0 ]
    [ "$output" = false ] || {
      echo "matched a non-publishing command: $cmd"
      return 1
    }
  done
}

@test "oneshot-publish-guard: a filing after a cd still counts" {
  _publishes 'cd x && gh issue create --title x'
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "oneshot-publish-guard: json and print are oneshot; rpc and tui are not" {
  run node --input-type=module -e "
    const { isOneshotSession } = await import('$REPO_ROOT/ai/pi/extensions/oneshot-publish-guard/detect.ts');
    const modes = ['json', 'print', 'rpc', 'tui'];
    process.stdout.write(modes.map((m) => m + '=' + isOneshotSession(m)).join(' '));
  "
  [ "$status" -eq 0 ]
  [ "$output" = "json=true print=true rpc=false tui=false" ]
}

@test "oneshot-publish-guard: detect.ts imports no SDK" {
  [ -f "$REPO_ROOT/ai/pi/extensions/oneshot-publish-guard/detect.ts" ]
  run grep -nE '^\s*import .*(@earendil-works/pi-coding-agent|isToolCallEventType)' \
    "$REPO_ROOT/ai/pi/extensions/oneshot-publish-guard/detect.ts"
  [ "$status" -ne 0 ]
}

# ── harness parity ───────────────────────────────────────────────────────────
# isPublishing (Pi) vs claude-bash-guard with a non-empty agent_id. Claude hook
# tests for the agent_id gate live in tests/claude_settings_writes.bats; this
# corpus only asks whether the two predicates agree.
#
# Classified by the block message, the way _agree in
# tests/pi_extensions_testpipe.bats is: a command the Claude hook refuses for
# an unrelated rule (compound cd, $VAR) is not a publish disagreement when Pi
# also declines to fire.

_pi_publish_verdict() {
  _publishes "$1"
  [ "$status" -eq 0 ]
  if [ "$output" = true ]; then
    echo publish
  else
    echo allowed
  fi
}

_claude_subagent_verdict() {
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"agent_id": "abc", "tool_input": {"command": sys.argv[1]}}))
' "$1")
  run _guard_in "$2" "$payload"
  case "$output" in
    *"does not file issues"*) echo publish ;;
    *"BLOCKED:"*) echo other ;;
    *) echo allowed ;;
  esac
}

_agree_publish() {
  local cmd="$1" sandbox="$2" claude pi
  pi=$(_pi_publish_verdict "$cmd")
  claude=$(_claude_subagent_verdict "$cmd" "$sandbox")
  if [ "$claude" = other ] && [ "$pi" = allowed ]; then
    return 0
  fi
  [ "$claude" = "$pi" ] || {
    echo "harness disagreement on: $cmd"
    echo "  claude=$claude  pi=$pi"
    return 1
  }
}

@test "oneshot-publish-guard: Claude subagent and Pi agree on the corpus" {
  local cmd sandbox
  sandbox="$(_review_sandbox "isaac/feat/x")"
  for cmd in \
    'gh issue create --title x --body y --repo nobody/nothing' \
    'linear issue create --team KEY --title x' \
    'gh pr create --draft' \
    'gh pr edit 1 --body x' \
    'gh pr comment 1 --body x' \
    'gh pr review 1 --comment x' \
    'gh api -X POST /repos/o/r/issues' \
    'gh api --method PATCH /repos/o/r/pulls/1' \
    'gh api --method=PUT /repos/o/r/issues/1' \
    'gh api -XDELETE /repos/o/r/issues/1/lock' \
    'gh api -X "POST" /repos/o/r/issues' \
    'gh api -X POST "/repos/o/r/issues"' \
    '"gh" issue create --title x' \
    'gh "issue" create --title x' \
    'pr create --draft' \
    'pr comments --post' \
    'pr describe --post' \
    'cd x && gh issue create --title x' \
    'gh issue list' \
    'gh pr view 1' \
    'gh api GET /repos/o/r/issues' \
    'gh api -X GET /repos/o/r/issues' \
    'gh api -X POST /repos/o/r/git/refs' \
    'gh pr merge 1' \
    'gh release create v1' \
    'gh --repo o/r issue create' \
    'GH_REPO=o/r gh issue create' \
    'pr review 123' \
    'pr review --self --fix' \
    'pr comments --triage' \
    'pr describe --draft' \
    "echo 'gh issue create'"; do
    _agree_publish "$cmd" "$sandbox"
  done
}
