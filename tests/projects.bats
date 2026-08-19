#!/usr/bin/env bats
# Tests for the project registry — lib/projects.sh, its Python half
# (ai/lib/workbench_projects.py), and the consumers that used to guess at where
# repos live: ai/claude/migrations/20260629-context-to-architecture.sh and
# ai/claude/skills/machine/generate-machine-profile.sh (#780).
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # Fully resolved: on macOS mktemp hands back a /var/folders path that git
  # reports as /private/var/folders, and half these assertions compare the two.
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_CACHE_DIR="$TMPDIR/cache"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"

  # Everything a test builds lives in a temp directory, which is precisely what
  # the default exclusion list refuses. The sandboxed state root still keeps the
  # writes out of the real registry.
  PROJECTS_EXCLUDED_PREFIXES=("$WORKBENCH_STATE_DIR" "$WORKBENCH_CACHE_DIR")

  # shellcheck source=../lib/ui.sh
  . "$REPO_ROOT/lib/ui.sh"
  PROJECTS_EXCLUDED_PREFIXES=("$WORKBENCH_STATE_DIR" "$WORKBENCH_CACHE_DIR")
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# make_repo DIR — a git work tree at DIR.
make_repo() {
  mkdir -p "$1"
  GIT_CEILING_DIRECTORIES="$(dirname "$1")" git -C "$1" init --quiet
}

# make_bare_container DIR — the layout wt-init produces: a bare repo at
# DIR/.git with per-branch worktrees beside it.
make_bare_container() {
  mkdir -p "$1"
  git init --bare --quiet "$1/.git"
}

# ─── Registration ────────────────────────────────────────────────────────────

@test "a registered repo comes back from project_registered" {
  make_repo "$TMPDIR/alpha"
  run project_register "$TMPDIR/alpha"
  [ "$status" -eq 0 ]

  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "registering twice leaves one line" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"

  run project_registered
  [ "${#lines[@]}" -eq 1 ]
}

@test "a trailing slash is the same repo" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha/"
  project_register "$TMPDIR/alpha"

  run project_registered
  [ "${#lines[@]}" -eq 1 ]
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "a directory that is not a git work tree is refused" {
  mkdir -p "$TMPDIR/plain"
  run project_register "$TMPDIR/plain"
  [ "$status" -eq 1 ]

  run project_registered
  [ -z "$output" ]
}

@test "a bare repo's container is refused" {
  make_bare_container "$TMPDIR/container"
  run project_register "$TMPDIR/container"
  [ "$status" -eq 1 ]
}

@test "a worktree inside a bare-repo container is registered" {
  make_bare_container "$TMPDIR/container"
  make_repo "$TMPDIR/container/main"
  run project_register "$TMPDIR/container/main"
  [ "$status" -eq 0 ]
}

@test "a relative path is refused" {
  run project_register "relative/path"
  [ "$status" -eq 1 ]
}

@test "a repo under an excluded prefix is refused" {
  make_repo "$WORKBENCH_STATE_DIR/reviews/wt"
  run project_register "$WORKBENCH_STATE_DIR/reviews/wt"
  [ "$status" -eq 1 ]
}

@test "temp paths are excluded by default" {
  # The default list, not this suite's override — this is the rule that keeps
  # every other bats suite's throwaway repos out of the real registry.
  unset PROJECTS_EXCLUDED_PREFIXES
  # shellcheck source=../lib/projects.sh
  . "$REPO_ROOT/lib/projects.sh"

  make_repo "$TMPDIR/alpha"
  run project_register "$TMPDIR/alpha"
  [ "$status" -eq 1 ]
}

# ─── Reads ───────────────────────────────────────────────────────────────────

@test "project_registered on a machine with no registry is empty and succeeds" {
  run project_registered
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a repo that has been deleted is skipped at read time" {
  make_repo "$TMPDIR/alpha"
  make_repo "$TMPDIR/beta"
  project_register "$TMPDIR/alpha"
  project_register "$TMPDIR/beta"
  rm -rf "$TMPDIR/alpha"

  run project_registered
  [ "$output" = "$TMPDIR/beta" ]
}

@test "comment lines are not paths" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"
  printf '# backfilled from somewhere\n' >> "$PROJECTS_REGISTRY_FILE"

  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "a final line with no newline after it is still read" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s' "$TMPDIR/alpha" > "$PROJECTS_REGISTRY_FILE"

  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

# ─── Forget and prune ────────────────────────────────────────────────────────

@test "project_forget drops one entry and keeps the rest" {
  make_repo "$TMPDIR/alpha"
  make_repo "$TMPDIR/beta"
  project_register "$TMPDIR/alpha"
  project_register "$TMPDIR/beta"

  run project_forget "$TMPDIR/alpha"
  [ "$status" -eq 0 ]

  run project_registered
  [ "$output" = "$TMPDIR/beta" ]
}

@test "project_forget on an unregistered repo fails" {
  run project_forget "$TMPDIR/nowhere"
  [ "$status" -eq 1 ]
}

@test "project_prune deletes the lines project_registered was skipping" {
  make_repo "$TMPDIR/alpha"
  make_repo "$TMPDIR/beta"
  project_register "$TMPDIR/alpha"
  project_register "$TMPDIR/beta"
  rm -rf "$TMPDIR/alpha"

  run project_prune
  [ "$output" = "1" ]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

@test "project_prune keeps the backfill marker" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  printf '# backfilled from somewhere\n%s\n' "$TMPDIR/gone" > "$PROJECTS_REGISTRY_FILE"

  run project_prune
  [ "$output" = "1" ]
  run grep -c 'backfilled from' "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

@test "project_prune on a machine with no registry reports nothing dropped" {
  run project_prune
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
}

# ─── Backfill ────────────────────────────────────────────────────────────────

@test "the backfill seeds the repos Claude Code recorded sessions in" {
  make_repo "$TMPDIR/alpha"
  mkdir -p "$TMPDIR/alpha/nested"
  CLAUDE_CONFIG_FILE="$TMPDIR/claude.json"
  printf '{"projects":{"%s":{},"%s":{}}}\n' "$TMPDIR/alpha" "$TMPDIR/alpha/nested" \
    > "$CLAUDE_CONFIG_FILE"

  run seed_project_registry
  [ "$status" -eq 0 ]

  # Both entries resolve to the same work-tree root, so one line, not two.
  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "the backfill runs once, even after the file already exists" {
  make_repo "$TMPDIR/alpha"
  CLAUDE_CONFIG_FILE="$TMPDIR/claude.json"
  printf '{"projects":{"%s":{}}}\n' "$TMPDIR/alpha" > "$CLAUDE_CONFIG_FILE"

  seed_project_registry
  project_forget "$TMPDIR/alpha"
  seed_project_registry

  run project_registered
  [ -z "$output" ]
}

@test "the backfill records itself even with nothing to seed" {
  CLAUDE_CONFIG_FILE="$TMPDIR/absent.json"
  run seed_project_registry
  [ "$status" -eq 0 ]
  run grep -c 'backfilled from' "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

@test "a session cwd that is no longer a repo is skipped, not fatal" {
  make_repo "$TMPDIR/alpha"
  CLAUDE_CONFIG_FILE="$TMPDIR/claude.json"
  printf '{"projects":{"%s":{},"%s":{}}}\n' "$TMPDIR/gone" "$TMPDIR/alpha" \
    > "$CLAUDE_CONFIG_FILE"

  run seed_project_registry
  [ "$status" -eq 0 ]
  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

# ─── Cross-language agreement ────────────────────────────────────────────────

@test "bash and Python name the same registry file" {
  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
print(workbench_projects.registry_path())
"
  [ "$status" -eq 0 ]
  [ "$output" = "$PROJECTS_REGISTRY_FILE" ]
}

@test "a repo Python registered is a repo bash reads" {
  make_repo "$TMPDIR/alpha"
  run python3 -c "
import os, sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
workbench_projects.TEMP_ROOTS = ()
os.environ.pop('TMPDIR', None)
assert workbench_projects.register('$TMPDIR/alpha')
"
  [ "$status" -eq 0 ]

  run project_registered
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "a repo bash registered is a repo Python reads" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"

  run python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
print(*workbench_projects.registered())
"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/alpha" ]
}

@test "both halves refuse a bare repo's container" {
  make_bare_container "$TMPDIR/container"
  run project_register "$TMPDIR/container"
  [ "$status" -eq 1 ]

  run python3 -c "
import os, sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_projects
workbench_projects.TEMP_ROOTS = ()
os.environ.pop('TMPDIR', None)
print(workbench_projects.register('$TMPDIR/container'))
"
  [ "$output" = "False" ]
}

# ─── Consumers ───────────────────────────────────────────────────────────────

# The migration and the profile generator both used to derive their own list of
# repos. These are the failures that produced (#780).

@test "the context-to-architecture migration reaches a repo past the old depth limit" {
  # Six levels below the root the old `find -maxdepth 5` walked. A bare-repo
  # container sits at exactly five, so any organisation one directory deeper was
  # invisible — and the migration recorded itself applied all the same.
  local deep="$TMPDIR/git/personal/otto-nation/some-repo/main/nested"
  make_repo "$deep"
  mkdir -p "$deep/.claude"
  echo "architecture" > "$deep/.claude/context.md"
  project_register "$deep"

  # shellcheck source=../ai/claude/migrations/20260629-context-to-architecture.sh
  . "$REPO_ROOT/ai/claude/migrations/20260629-context-to-architecture.sh"
  run migration_20260629_context_to_architecture
  [ "$status" -eq 0 ]

  [ ! -f "$deep/.claude/context.md" ]
  [ "$(cat "$deep/.claude/architecture.md")" = "architecture" ]
}

@test "the context-to-architecture migration leaves an existing architecture.md alone" {
  make_repo "$TMPDIR/alpha"
  mkdir -p "$TMPDIR/alpha/.claude"
  echo "old" > "$TMPDIR/alpha/.claude/context.md"
  echo "current" > "$TMPDIR/alpha/.claude/architecture.md"
  project_register "$TMPDIR/alpha"

  # shellcheck source=../ai/claude/migrations/20260629-context-to-architecture.sh
  . "$REPO_ROOT/ai/claude/migrations/20260629-context-to-architecture.sh"
  run migration_20260629_context_to_architecture
  [ "$status" -eq 0 ]
  [ "$(cat "$TMPDIR/alpha/.claude/architecture.md")" = "current" ]
}

@test "the machine profile names the workbench and the registered repos" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]

  local profile="$TMPDIR/home/.claude/machine/machine.md"
  local named
  named="$(sed -n 's|^- otto-workbench: ||p' "$profile")"
  # Whatever it names is a real workbench checkout — which the three hardcoded
  # candidates could not promise.
  [ -f "$named/lib/constants.sh" ]
  grep -q "| alpha | $TMPDIR/alpha |" "$profile"
}

@test "the machine profile reports an unresolvable workbench location" {
  # The three hardcoded candidate paths this replaced matched nothing on a
  # bare-repo machine, so workbench_dir came out empty and the profile silently
  # omitted the line — with nothing anywhere reporting the miss.
  export WORKBENCH_STABLE_DIR="$TMPDIR/no-such-workbench"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  [[ "$output" == *"did not resolve"* ]]
  grep -q '^- otto-workbench: location unresolved' "$TMPDIR/home/.claude/machine/machine.md"
}

@test "the machine profile says so when nothing is registered" {
  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  grep -q '^## Project Registry' "$TMPDIR/home/.claude/machine/machine.md"
  grep -q 'No repos registered yet' "$TMPDIR/home/.claude/machine/machine.md"
}
