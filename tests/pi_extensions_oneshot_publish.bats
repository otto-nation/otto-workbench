#!/usr/bin/env bats
# Tests for oneshot-publish-guard: refuse issue/PR writes in json/print sessions.
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
    'pr create --draft'; do
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

# ── claude-bash-guard (agent_id) ─────────────────────────────────────────────
# The same command set, keyed on a non-empty agent_id rather than session mode.
# Uses _guard_in so these run without claude_settings_writes.bats' setup_file.

_claude_publish() {
  local cmd="$1" agent="$2" sandbox payload
  sandbox="$(_review_sandbox "isaac/feat/x")"
  payload=$(python3 -c '
import json, sys
agent = None if sys.argv[2] == "null" else sys.argv[2]
print(json.dumps({"tool_input": {"command": sys.argv[1]}, "agent_id": agent}))
' "$cmd" "$agent")
  run _guard_in "$sandbox" "$payload"
}

@test "claude-bash-guard: subagent gh issue create is denied" {
  _claude_publish 'gh issue create --title x --body y' 'a7175461c26907157'
  [ "$status" -eq 2 ]
  [[ "$output" == *"subagent"* ]]
}

@test "claude-bash-guard: main session gh issue create is allowed" {
  _claude_publish 'gh issue create --title x --body y' 'null'
  [ "$status" -eq 0 ]
}

@test "claude-bash-guard: subagent gh pr view is allowed" {
  _claude_publish 'gh pr view 1' 'a7175461c26907157'
  [ "$status" -eq 0 ]
}
