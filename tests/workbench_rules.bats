#!/usr/bin/env bats
# Tests for workbench-rules — domain normalization, add/list/status commands,
# the generated layer, and project-level rule management.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  WORKBENCH_RULES="$REPO_ROOT/ai/bin/workbench-rules"

  # Source for function-level tests
  export HOME="$TMPDIR"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export NO_COLOR=1
  OVERRIDE_RULES="$WORKBENCH_CONFIG_DIR/overrides/ai/guidelines/rules"
  GENERATED_RULES="$WORKBENCH_STATE_DIR/rules"
  # shellcheck source=/dev/null
  source "$WORKBENCH_RULES"

  # Fake source tree for cmd_sync tests — modeled on skills_install.bats.
  FAKE_WORKBENCH="$TMPDIR/workbench"
  mkdir -p "$FAKE_WORKBENCH/ai/guidelines/rules"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: run workbench-rules CLI with the sandboxed roots
_run_rules() {
  NO_COLOR=1 run "$WORKBENCH_RULES" "$@"
}

# _run_sync — runs `workbench-rules sync` against the fake workbench's rules tree.
_run_sync() {
  WORKBENCH_DIR="$FAKE_WORKBENCH" WORKBENCH_SYNC=true NO_COLOR=1 \
    run "$WORKBENCH_RULES" sync
}

# _make_repo DIR — a real repo with one commit at DIR.
#
# A `mkdir DIR/.git` stand-in is not enough: the root these commands write into
# comes from git itself, so a directory that only looks like a repo is not one.
_make_repo() {
  mkdir -p "$1"
  git -C "$1" init -q
  git -C "$1" config user.email test@example.com
  git -C "$1" config user.name Test
  git -C "$1" commit -q --allow-empty -m init
}

# ── _normalize_domain ────────────────────────────────────────────────────────

@test "_normalize_domain: ts maps to typescript" {
  result=$(_normalize_domain "ts")
  [ "$result" = "typescript" ]
}

@test "_normalize_domain: js maps to typescript" {
  result=$(_normalize_domain "js")
  [ "$result" = "typescript" ]
}

@test "_normalize_domain: py maps to python" {
  result=$(_normalize_domain "py")
  [ "$result" = "python" ]
}

@test "_normalize_domain: sh maps to bash" {
  result=$(_normalize_domain "sh")
  [ "$result" = "bash" ]
}

@test "_normalize_domain: shell maps to bash" {
  result=$(_normalize_domain "shell")
  [ "$result" = "bash" ]
}

@test "_normalize_domain: yml maps to yaml" {
  result=$(_normalize_domain "yml")
  [ "$result" = "yaml" ]
}

@test "_normalize_domain: unknown domain passes through" {
  result=$(_normalize_domain "go")
  [ "$result" = "go" ]
}

@test "_normalize_domain: custom domain passes through" {
  result=$(_normalize_domain "kubernetes")
  [ "$result" = "kubernetes" ]
}

# ── CLI: --help ──────────────────────────────────────────────────────────────

@test "workbench-rules --help exits 0" {
  _run_rules --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"workbench-rules"* ]]
}

@test "workbench-rules -h exits 0" {
  _run_rules -h
  [ "$status" -eq 0 ]
}

@test "workbench-rules no args exits non-zero" {
  _run_rules
  [ "$status" -ne 0 ]
}

# ── CLI: add ─────────────────────────────────────────────────────────────────

@test "add: creates local rule file" {
  _run_rules add go "use errors.As"
  [ "$status" -eq 0 ]
  [ -f "$OVERRIDE_RULES/go.local.md" ]
  grep -q "use errors.As" "$OVERRIDE_RULES/go.local.md"
}

@test "add: writes into the override layer, not a harness's rules directory" {
  # The layer moved out of ~/.claude/rules/ because a machine without Claude
  # Code could not reach it there — the whole point of the rename.
  _run_rules add go "use errors.As"
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.claude" ]
}

@test "add: appends to existing file" {
  _run_rules add go "first rule"
  _run_rules add go "second rule"
  [ "$status" -eq 0 ]
  local count
  count=$(grep -c "^- " "$OVERRIDE_RULES/go.local.md")
  [ "$count" -eq 2 ]
}

@test "add: missing domain exits non-zero" {
  _run_rules add
  [ "$status" -ne 0 ]
  [[ "$output" == *"Usage"* ]]
}

@test "add: missing rule text exits non-zero" {
  _run_rules add go
  [ "$status" -ne 0 ]
  [[ "$output" == *"Usage"* ]]
}

@test "add: normalizes domain aliases" {
  _run_rules add ts "prefer const"
  [ "$status" -eq 0 ]
  [ -f "$OVERRIDE_RULES/ts.local.md" ]
}

# ── CLI: list ────────────────────────────────────────────────────────────────

@test "list: no rules shows 'No local rule files'" {
  mkdir -p "$OVERRIDE_RULES"
  _run_rules list
  [ "$status" -eq 0 ]
  [[ "$output" == *"No local rule files"* ]]
}

@test "list: shows existing local rule files" {
  mkdir -p "$OVERRIDE_RULES"
  echo "- test rule" > "$OVERRIDE_RULES/go.local.md"
  _run_rules list
  [ "$status" -eq 0 ]
  [[ "$output" == *"go.local.md"* ]]
}

@test "list: no rules directory shows appropriate message" {
  _run_rules list
  [ "$status" -eq 0 ]
  [[ "$output" == *"No rules directory"* ]]
}

# ── CLI: status ──────────────────────────────────────────────────────────────

@test "status: no local rules shows clean message" {
  mkdir -p "$OVERRIDE_RULES"
  _run_rules status
  [ "$status" -eq 0 ]
  [[ "$output" == *"No untracked"* ]]
}

@test "status: shows content of local rules" {
  mkdir -p "$OVERRIDE_RULES"
  echo "- my custom rule" > "$OVERRIDE_RULES/go.local.md"
  _run_rules status
  [ "$status" -eq 0 ]
  [[ "$output" == *"go.local.md"* ]]
  [[ "$output" == *"my custom rule"* ]]
}

@test "status: skips frontmatter in display" {
  mkdir -p "$OVERRIDE_RULES"
  cat > "$OVERRIDE_RULES/go.local.md" <<'EOF'
---
description: Go rules
---
- actual rule
EOF
  _run_rules status
  [ "$status" -eq 0 ]
  [[ "$output" == *"actual rule"* ]]
  [[ "$output" != *"description:"* ]]
}

# ── CLI: project ─────────────────────────────────────────────────────────────

@test "project add: appends rule to CLAUDE.md" {
  local repo="$TMPDIR/myrepo"
  _make_repo "$repo"
  cat > "$repo/CLAUDE.md" <<'EOF'
# My Project

## Conventions

- existing rule
EOF
  cd "$repo"
  _run_rules project add "new rule"
  [ "$status" -eq 0 ]
  grep -q "new rule" "$repo/CLAUDE.md"
}

@test "project add: creates Conventions section if missing" {
  local repo="$TMPDIR/myrepo"
  _make_repo "$repo"
  echo "# My Project" > "$repo/CLAUDE.md"
  cd "$repo"
  _run_rules project add "first convention"
  [ "$status" -eq 0 ]
  grep -q "## Conventions" "$repo/CLAUDE.md"
  grep -q "first convention" "$repo/CLAUDE.md"
}

@test "project add: fails without CLAUDE.md" {
  local repo="$TMPDIR/myrepo"
  _make_repo "$repo"
  cd "$repo"
  _run_rules project add "some rule"
  [ "$status" -ne 0 ]
  [[ "$output" == *"No CLAUDE.md"* ]]
}

@test "project add: fails outside git repo" {
  cd "$TMPDIR"
  _run_rules project add "some rule"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Not inside a git"* ]]
}

@test "project show: displays CLAUDE.md content" {
  local repo="$TMPDIR/myrepo"
  _make_repo "$repo"
  echo "# Test Content" > "$repo/CLAUDE.md"
  cd "$repo"
  _run_rules project show
  [ "$status" -eq 0 ]
  [[ "$output" == *"Test Content"* ]]
}

@test "project add: writes the worktree's CLAUDE.md from a bare container" {
  local seed="$TMPDIR/seed" container="$TMPDIR/container"
  mkdir -p "$seed"
  printf '# Seed\n\n## Conventions\n\n- existing rule\n' > "$seed/CLAUDE.md"
  make_container_seed "$seed"
  make_worktree_container "$container" "$seed"

  # A container holds the bare .git and the checkouts as peers, so the only
  # .git *directory* above the worktree is the container's own. Walking up for
  # one lands there, where a CLAUDE.md is tracked by nothing and read by no
  # session.
  cd "$container"
  _run_rules project add "new rule"
  [ "$status" -eq 0 ]
  grep -q "new rule" "$container/main/CLAUDE.md"
  [ ! -e "$container/CLAUDE.md" ]
}

@test "project add: fails rather than write into a container with no worktree" {
  local seed="$TMPDIR/seed" container="$TMPDIR/container"
  mkdir -p "$seed"
  printf '# Seed\n\n## Conventions\n\n- existing rule\n' > "$seed/CLAUDE.md"
  make_container_seed "$seed"
  make_empty_container "$container" "$seed"

  cd "$container"
  _run_rules project add "new rule"
  [ "$status" -ne 0 ]
  [[ "$output" == *"No worktree resolved"* ]]
  [ ! -e "$container/CLAUDE.md" ]
}

# ── CLI: sync ────────────────────────────────────────────────────────────────

@test "sync: writes workbench.md into the generated layer" {
  _run_sync
  [ "$status" -eq 0 ]
  [ -f "$GENERATED_RULES/workbench.md" ]
  grep -q "$FAKE_WORKBENCH" "$GENERATED_RULES/workbench.md"
}

@test "sync: installs into no harness" {
  # Each harness installs the merged set in its own step. A CLI that also laid
  # the rules out for one of them is what coupled Pi's context file to Claude
  # Code's sync having run.
  printf -- '# Shared\n' > "$FAKE_WORKBENCH/ai/guidelines/rules/shared.md"

  _run_sync
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.claude" ]
  [ ! -e "$HOME/.pi" ]
}

@test "sync: is idempotent" {
  _run_sync
  cp "$GENERATED_RULES/workbench.md" "$TMPDIR/first"
  _run_sync
  run diff "$TMPDIR/first" "$GENERATED_RULES/workbench.md"
  [ "$status" -eq 0 ]
}

@test "sync: fails when the workbench has no rules tree" {
  rm -rf "$FAKE_WORKBENCH/ai/guidelines/rules"
  _run_sync
  [ "$status" -ne 0 ]
  [[ "$output" == *"Rules not found"* ]]
}
