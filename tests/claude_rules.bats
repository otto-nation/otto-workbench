#!/usr/bin/env bats
# Tests for claude-rules — domain normalization, add/list/status commands,
# and project-level rule management.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  CLAUDE_RULES="$REPO_ROOT/ai/claude/bin/claude-rules"

  # Source for function-level tests
  export HOME="$TMPDIR"
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$CLAUDE_RULES"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: run claude-rules CLI with overridden HOME
_run_rules() {
  HOME="$TMPDIR" NO_COLOR=1 run "$CLAUDE_RULES" "$@"
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

@test "claude-rules --help exits 0" {
  _run_rules --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"claude-rules"* ]]
}

@test "claude-rules -h exits 0" {
  _run_rules -h
  [ "$status" -eq 0 ]
}

@test "claude-rules no args exits non-zero" {
  _run_rules
  [ "$status" -ne 0 ]
}

# ── CLI: add ─────────────────────────────────────────────────────────────────

@test "add: creates local rule file" {
  _run_rules add go "use errors.As"
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/.claude/rules/go.local.md" ]
  grep -q "use errors.As" "$TMPDIR/.claude/rules/go.local.md"
}

@test "add: appends to existing file" {
  _run_rules add go "first rule"
  _run_rules add go "second rule"
  [ "$status" -eq 0 ]
  local count
  count=$(grep -c "^- " "$TMPDIR/.claude/rules/go.local.md")
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
  [ -f "$TMPDIR/.claude/rules/ts.local.md" ]
}

# ── CLI: list ────────────────────────────────────────────────────────────────

@test "list: no rules shows 'No local rule files'" {
  mkdir -p "$TMPDIR/.claude/rules"
  _run_rules list
  [ "$status" -eq 0 ]
  [[ "$output" == *"No local rule files"* ]]
}

@test "list: shows existing local rule files" {
  mkdir -p "$TMPDIR/.claude/rules"
  echo "- test rule" > "$TMPDIR/.claude/rules/go.local.md"
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
  mkdir -p "$TMPDIR/.claude/rules"
  _run_rules status
  [ "$status" -eq 0 ]
  [[ "$output" == *"No untracked"* ]]
}

@test "status: shows content of local rules" {
  mkdir -p "$TMPDIR/.claude/rules"
  echo "- my custom rule" > "$TMPDIR/.claude/rules/go.local.md"
  _run_rules status
  [ "$status" -eq 0 ]
  [[ "$output" == *"go.local.md"* ]]
  [[ "$output" == *"my custom rule"* ]]
}

@test "status: skips frontmatter in display" {
  mkdir -p "$TMPDIR/.claude/rules"
  cat > "$TMPDIR/.claude/rules/go.local.md" <<'EOF'
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
