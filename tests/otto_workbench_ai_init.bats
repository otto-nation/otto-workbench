#!/usr/bin/env bats
# Tests for `otto-workbench ai init` — which tree the .claude/ scaffold lands in.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  OTTO="$REPO_ROOT/bin/otto-workbench"
  # The CLI reaches the scaffold through component discovery, which select-tests
  # cannot follow; naming the file maps this suite to the code it exercises.
  # SCAFFOLD_SH is otherwise unused below by design — it exists only for
  # select-tests to grep, so do not remove it as dead code.
  SCAFFOLD_SH="$REPO_ROOT/ai/claude/scaffold.sh"
  [ -f "$SCAFFOLD_SH" ]
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

@test "ai init writes AGENTS.md at the worktree root, not inside .claude/" {
  # Both harnesses resolve the instructions file at the root; Pi never looks
  # inside .claude/, and Claude Code reads AGENTS.md there natively.
  local container="$TMPDIR/c"
  make_worktree_container "$container" "$SEED"

  run _run_in "$container" ai init
  [ "$status" -eq 0 ]
  [ -f "$container/main/AGENTS.md" ]
  [ ! -e "$container/main/CLAUDE.md" ]
  [ ! -e "$container/main/.claude/CLAUDE.md" ]
  # The file is the repo's, shared with people who may not use the workbench.
  run grep -E "ai sync|otto-workbench|claude update" "$container/main/AGENTS.md"
  [ "$status" -ne 0 ]
}

@test "ai init --force overwrites a hand-authored root AGENTS.md" {
  local container="$TMPDIR/c"
  make_worktree_container "$container" "$SEED"
  printf 'HAND-AUTHORED CONTENT\n' > "$container/main/AGENTS.md"

  run _run_in "$container" ai init --force
  [ "$status" -eq 0 ]
  [ -f "$container/main/AGENTS.md" ]
  [ "$(cat "$container/main/AGENTS.md")" != "HAND-AUTHORED CONTENT" ]
}

@test "ai init leaves a repo still on CLAUDE.md alone and suggests the rename" {
  # A fresh AGENTS.md beside it would split the harnesses: Claude Code would
  # read only CLAUDE.md and Pi only AGENTS.md.
  local container="$TMPDIR/c"
  make_worktree_container "$container" "$SEED"
  printf 'LEGACY RULES\n' > "$container/main/CLAUDE.md"

  run _run_in "$container" ai init
  [ "$status" -eq 0 ]
  [ ! -e "$container/main/AGENTS.md" ]
  [ "$(cat "$container/main/CLAUDE.md")" = "LEGACY RULES" ]
  [[ "$output" == *"git mv CLAUDE.md AGENTS.md"* ]]
}

@test "ai init --force on a repo still on CLAUDE.md writes AGENTS.md and names the leftover" {
  local container="$TMPDIR/c"
  make_worktree_container "$container" "$SEED"
  printf 'LEGACY RULES\n' > "$container/main/CLAUDE.md"

  run _run_in "$container" ai init --force
  [ "$status" -eq 0 ]
  [ -f "$container/main/AGENTS.md" ]
  [ "$(cat "$container/main/CLAUDE.md")" = "LEGACY RULES" ]
  [[ "$output" == *"CLAUDE.md is still here"* ]]
}
