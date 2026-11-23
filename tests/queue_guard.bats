#!/usr/bin/env bats
# The two harness halves of the queue-admission guard, held against each other
# and against bin/local/queue-guard-check, which owns the decision.
#
# Claude half: ai/claude/bin/claude-bash-guard.
# Pi half: ai/pi/extensions/queue-guard/detect.ts.
#
# select-on: bin/local/queue-guard-check ai/claude/bin/claude-bash-guard ai/pi/extensions/queue-guard/detect.ts

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  unset OTTO_QUEUE_ENTRY
  LISTED="$TMPDIR/listed"
  UNLISTED="$TMPDIR/unlisted"
  git init -q "$LISTED"
  git init -q "$UNLISTED"
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf 'queue:\n  heavy_repos: [listed]\n' > "$WORKBENCH_CONFIG_DIR/config.yml"
  export WORKBENCH_CONFIG_DIR
}

teardown() {
  common_teardown
}

# _claude CWD COMMAND — 0 to allow, 2 to refuse. Stderr is kept for the reason.
_claude() {
  run --separate-stderr bash -c '
    jq -n --arg cwd "$1" --arg cmd "$2" "{cwd: \$cwd, tool_input: {command: \$cmd}}" \
      | "$3/ai/claude/bin/claude-bash-guard"
  ' _ "$1" "$2" "$REPO_ROOT"
}

# _pi CWD COMMAND — prints the refusal, or nothing.
_pi() {
  run node --input-type=module -e "
    import { commandRefusal } from '$REPO_ROOT/ai/pi/extensions/queue-guard/detect.ts';
    const reason = commandRefusal(process.argv[1], process.argv[2]);
    if (reason) process.stdout.write(reason);
  " "$2" "$1"
}

@test "Claude Code refuses a heavy command in a listed repo and names the queued form" {
  _claude "$LISTED" "golangci-lint run ./..."
  [ "$status" -eq 2 ]
  # shellcheck disable=SC2154  # stderr is set by bats' run --separate-stderr
  [[ "$stderr" == *"otto-queue submit --class heavy -- golangci-lint run ./..."* ]]
}

@test "Pi refuses the same command with the same reason" {
  _claude "$LISTED" "go test ./..."
  local claude_reason="${stderr#BLOCKED: }"
  _pi "$LISTED" "go test ./..."
  [ "$status" -eq 0 ]
  [ "$output" = "$claude_reason" ]
}

@test "both harnesses let an unlisted repo and a non-heavy command through" {
  _claude "$UNLISTED" "go test ./..."
  [ "$status" -eq 0 ]
  _pi "$UNLISTED" "go test ./..."
  [ -z "$output" ]
  _claude "$LISTED" "ls -la"
  [ "$status" -eq 0 ]
  _pi "$LISTED" "ls -la"
  [ -z "$output" ]
}

@test "both harnesses admit a command already run through otto-queue" {
  _claude "$LISTED" "otto-queue run --ticket --class heavy -- go test ./..."
  [ "$status" -eq 0 ]
  _pi "$LISTED" "otto-queue run --ticket --class heavy -- go test ./..."
  [ -z "$output" ]
  _claude "$LISTED" "otto-queue submit --class heavy -- go test ./..."
  [ "$status" -eq 0 ]
  _pi "$LISTED" "otto-queue submit --class heavy -- go test ./..."
  [ -z "$output" ]
  _claude "$LISTED" "otto-queue wait 12"
  [ "$status" -eq 0 ]
  _pi "$LISTED" "otto-queue wait 12"
  [ -z "$output" ]
}
