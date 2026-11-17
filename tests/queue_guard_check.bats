#!/usr/bin/env bats
# Tests for bin/local/queue-guard-check — which agent commands must be queued.
#
# select-on: bin/local/queue-guard-check ai/lib/config/workbench_config.py

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME"
  export HOME="$TEST_HOME"
  gate_sandbox
  CHECK="$REPO_ROOT/bin/local/queue-guard-check"
  unset OTTO_QUEUE_ENTRY

  # A bare-repo container named "maximum" with a main worktree, and a plain
  # clone named "solo".
  CONTAINER="$TEST_HOME/maximum"
  git init -q --bare "$CONTAINER/.git"
  local tree commit
  tree="$(git --git-dir="$CONTAINER/.git" hash-object -t tree -w /dev/null)"
  commit="$(GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t \
    git --git-dir="$CONTAINER/.git" commit-tree -m init "$tree")"
  git --git-dir="$CONTAINER/.git" update-ref refs/heads/main "$commit"
  git --git-dir="$CONTAINER/.git" worktree add -q "$CONTAINER/main" main
  SOLO="$TEST_HOME/solo"
  git init -q "$SOLO"
}

teardown() {
  common_teardown
}

_list() {
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf 'queue:\n  heavy_repos: [%s]\n' "$1" > "$WORKBENCH_CONFIG_DIR/config.yml"
}

_check() {
  run env WORKBENCH_CONFIG_DIR="$WORKBENCH_CONFIG_DIR" "$CHECK" "$1" "$2"
}

@test "a heavy command in a listed bare-repo container is refused with the queued form" {
  _list "maximum"
  _check "golangci-lint run ./..." "$CONTAINER/main"
  [ "$status" -eq 0 ]
  [[ "$output" == *"otto-queue submit --class heavy -- golangci-lint run ./..."* ]]
  [[ "$output" == *"otto-queue wait <id>"* ]]
}

@test "a listed plain clone is named by its toplevel directory" {
  _list "solo"
  _check "go test ./..." "$SOLO"
  [ "$status" -eq 0 ]
}

@test "a repo that is not listed runs freely" {
  _list "other"
  _check "golangci-lint run" "$CONTAINER/main"
  [ "$status" -eq 1 ]
  [ -z "$output" ]
}

@test "an empty list runs everything freely" {
  _list ""
  _check "golangci-lint run" "$CONTAINER/main"
  [ "$status" -eq 1 ]
}

@test "a command that is not heavy runs freely in a listed repo" {
  _list "maximum"
  for cmd in "ls -la" "go build ./..." "go vet ./..." "git status" "tasks build"; do
    _check "$cmd" "$CONTAINER/main"
    [ "$status" -eq 1 ]
  done
}

@test "heavy commands are found per statement and behind env assignments" {
  _list "maximum"
  _check "cd src && go test ./pkg" "$CONTAINER/main"
  [ "$status" -eq 0 ]
  [[ "$output" == *"-- go test ./pkg"* ]]
  _check "CI=1 tasks lint go" "$CONTAINER/main"
  [ "$status" -eq 0 ]
  [[ "$output" == *"-- tasks lint go"* ]]
  _check "make && /usr/local/bin/golangci-lint run" "$CONTAINER/main"
  [ "$status" -eq 0 ]
}

@test "a statement already behind otto-queue is admitted" {
  _list "maximum"
  _check "otto-queue run --ticket --class heavy -- go test ./..." "$CONTAINER/main"
  [ "$status" -eq 1 ]
}

@test "inside a queued job nothing is refused" {
  _list "maximum"
  run env OTTO_QUEUE_ENTRY=7 WORKBENCH_CONFIG_DIR="$WORKBENCH_CONFIG_DIR" "$CHECK" "go test ./..." "$CONTAINER/main"
  [ "$status" -eq 1 ]
}

@test "a cwd that is not a git repository is let through" {
  _list "maximum"
  _check "go test ./..." "$TEST_HOME"
  [ "$status" -eq 1 ]
}

@test "missing arguments are a usage error" {
  run "$CHECK" "go test"
  [ "$status" -eq 64 ]
}
