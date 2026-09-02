#!/usr/bin/env bats
# Tests for `otto-workbench ai init` — which tree the .claude/ scaffold lands in.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  OTTO="$REPO_ROOT/bin/otto-workbench"
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  sandbox_state_dir
  SEED="$TMPDIR/seed"
  mkdir -p "$SEED"
  printf 'x\n' > "$SEED/a.sh"
  make_container_seed "$SEED"

  # A scaffold that lands offers to run /analyze-project against it. The stub
  # keeps that offer from reaching the real CLI; stdin comes from /dev/null in
  # _run_in, so the prompt takes its default rather than waiting for a key.
  mkdir -p "$TMPDIR/bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$TMPDIR/bin/claude"
  chmod +x "$TMPDIR/bin/claude"
  PATH="$TMPDIR/bin:$PATH"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _run_in DIR ARGS... — otto-workbench with DIR as the working directory.
_run_in() {
  local dir="$1"
  shift
  cd "$dir" || return 1
  "$OTTO" "$@" < /dev/null
}

@test "ai init scaffolds the worktree, not the container it was run from" {
  local container="$TMPDIR/c"
  make_worktree_container "$container" "$SEED"

  run _run_in "$container" ai init
  [ "$status" -eq 0 ]
  [ -d "$container/main/.claude" ]
  [ ! -e "$container/.claude" ]
}

@test "ai init skips outside any repository" {
  mkdir -p "$TMPDIR/loose"

  run _run_in "$TMPDIR/loose" ai init
  [ "$status" -eq 0 ]
  [[ "$output" == *"Not in a git repo"* ]]
  [ ! -e "$TMPDIR/loose/.claude" ]
}

@test "ai init skips a container with no worktree rather than scaffolding it" {
  # A .claude/ tree at a container root is tracked by nothing and read by no
  # session, and no .gitignore rule, review, or CI check reaches inside a bare
  # repo to say so. Skipping is the only honest answer when the container names
  # no worktree to scaffold instead.
  local container="$TMPDIR/c"
  make_empty_container "$container" "$SEED"

  run _run_in "$container" ai init
  [ "$status" -eq 0 ]
  [[ "$output" == *"No worktree resolved"* ]]
  [ ! -e "$container/.claude" ]
}

@test "ai init writes the context file at the worktree root, not inside .claude/" {
  # Pi's ancestor walk reads one context file per directory root and never looks
  # inside .claude/ — a file there is invisible to it while Claude Code reads it,
  # which is exactly the gap that stays silent.
  local container="$TMPDIR/c"
  make_worktree_container "$container" "$SEED"

  run _run_in "$container" ai init
  [ "$status" -eq 0 ]
  [ -f "$container/main/CLAUDE.md" ]
  [ ! -e "$container/main/.claude/CLAUDE.md" ]
}

@test "ai init --force overwrites a hand-authored root CLAUDE.md" {
  # --force now targets the file every harness reads first, not a nested
  # .claude/CLAUDE.md — confirm it still overwrites deliberately rather than
  # silently skipping or landing somewhere else.
  local container="$TMPDIR/c"
  make_worktree_container "$container" "$SEED"
  printf 'HAND-AUTHORED CONTENT\n' > "$container/main/CLAUDE.md"

  run _run_in "$container" ai init --force
  [ "$status" -eq 0 ]
  [ -f "$container/main/CLAUDE.md" ]
  [ "$(cat "$container/main/CLAUDE.md")" != "HAND-AUTHORED CONTENT" ]
}
