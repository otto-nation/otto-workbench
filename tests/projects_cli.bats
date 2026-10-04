#!/usr/bin/env bats
# Tests for the `otto-workbench projects` CLI and the registry's consumers.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  load 'projects_helper'
  # Fully resolved: on macOS mktemp hands back a /var/folders path that git
  # reports as /private/var/folders, and half these assertions compare the two.
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_CACHE_DIR="$TMPDIR/cache"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"

  # Everything a test builds lives in a temp directory, which is precisely what
  # the default exclusion list refuses. The sandboxed state root still keeps the
  # writes out of the real registry.
  # shellcheck disable=SC2034  # read by lib/projects.sh
  PROJECTS_EXCLUDED_PREFIXES=("$WORKBENCH_STATE_DIR" "$WORKBENCH_CACHE_DIR")

  # shellcheck source=../lib/ui.sh
  . "$REPO_ROOT/lib/ui.sh"
}

teardown() {
  common_teardown
}

# ─── CLI ─────────────────────────────────────────────────────────────────────

# `otto-workbench projects` is the surface an operator corrects the registry
# from — the answer to a repo that joined late or one that should never have.

@test "projects list says so when nothing is registered" {
  run "$REPO_ROOT/bin/otto-workbench" projects list
  [ "$status" -eq 0 ]
  [[ "$output" == *"No repos registered yet"* ]]
}

@test "projects with no subcommand lists" {
  run "$REPO_ROOT/bin/otto-workbench" projects
  [ "$status" -eq 0 ]
  [[ "$output" == *"No repos registered yet"* ]]
}

@test "projects list names each registered repo and counts them" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  make_repo "$TMPDIR/beta"
  printf '%s\n%s\n' "$TMPDIR/alpha" "$TMPDIR/beta" > "$PROJECTS_REGISTRY_FILE"

  run "$REPO_ROOT/bin/otto-workbench" projects list
  [ "$status" -eq 0 ]
  [[ "$output" == *"$TMPDIR/alpha"* ]]
  [[ "$output" == *"$TMPDIR/beta"* ]]
  [[ "$output" == *"2 repo(s)"* ]]
}

@test "projects list groups a repo's worktrees under it" {
  # The repos are what the count is of. A machine that cuts a worktree per
  # branch used to read as a dozen separate projects.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_bare_worktree_layout "$TMPDIR/container"
  printf '%s\n%s\n' "$TMPDIR/container/main" "$TMPDIR/container/feature" \
    > "$PROJECTS_REGISTRY_FILE"

  run "$REPO_ROOT/bin/otto-workbench" projects list
  [ "$status" -eq 0 ]
  [[ "$output" == *"  $TMPDIR/container"* ]]
  [[ "$output" == *"    main"* ]]
  [[ "$output" == *"    feature"* ]]
  [[ "$output" == *"1 repo(s), 2 work tree(s)"* ]]
}

@test "projects list names an ordinary clone once" {
  # Its only work tree is the repo itself, so a line under it would repeat the
  # line above it word for word.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\n' "$TMPDIR/alpha" > "$PROJECTS_REGISTRY_FILE"

  run "$REPO_ROOT/bin/otto-workbench" projects list
  [ "$status" -eq 0 ]
  run grep -c "$TMPDIR/alpha" <<< "$output"
  [ "$output" = "1" ]
}

@test "projects list writes a path under HOME with a tilde" {
  # The replacement is tilde-expanded before it is substituted in, so an
  # unescaped ~ puts $HOME back and every path printed in full.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\n' "$TMPDIR/alpha" > "$PROJECTS_REGISTRY_FILE"

  HOME="$TMPDIR" run "$REPO_ROOT/bin/otto-workbench" projects list
  [ "$status" -eq 0 ]
  [[ "$output" == *"~/alpha"* ]]
}

@test "projects add refuses a directory that is not a work tree" {
  mkdir -p "$TMPDIR/plain"
  run "$REPO_ROOT/bin/otto-workbench" projects add "$TMPDIR/plain"
  [ "$status" -eq 1 ]
  [[ "$output" == *"Not a git work tree"* ]]
}

@test "projects add refuses a repo under a temporary path" {
  # An array cannot be exported, so the subprocess uses the real default
  # exclusion list — this is the one test that drives it end to end.
  make_repo "$TMPDIR/alpha"
  run "$REPO_ROOT/bin/otto-workbench" projects add "$TMPDIR/alpha"
  [ "$status" -eq 1 ]
  [[ "$output" == *"temporary path"* ]]

  run project_registered
  [ -z "$output" ]
}

@test "a repo that qualifies but cannot be written is not called 'not a project'" {
  # project_register returns 1 for a refusal and 2 for a write that failed. A
  # read-only state root used to read back as "a temporary path or a bare repo's
  # container", which sends the user looking at the wrong thing entirely.
  skip_if_root
  make_repo "$TMPDIR/alpha"
  mkdir -p "$WORKBENCH_STATE_DIR"
  chmod 500 "$WORKBENCH_STATE_DIR"

  run project_register "$TMPDIR/alpha"
  chmod 700 "$WORKBENCH_STATE_DIR"
  [ "$status" -eq 2 ]
}

@test "projects forget resolves a path holding .. to the stored entry" {
  # Entries are stored as `git rev-parse --show-toplevel` returned them and
  # forget matches by exact string, so anything an operator can reasonably type
  # has to be canonicalised first or a valid request reads as "not registered".
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/canon/repo"
  mkdir -p "$TMPDIR/canon/repo/sub"
  printf '%s\n' "$TMPDIR/canon/repo" > "$PROJECTS_REGISTRY_FILE"

  run "$REPO_ROOT/bin/otto-workbench" projects forget "$TMPDIR/canon/repo/sub/.."
  [ "$status" -eq 0 ]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "0" ]
}

@test "projects forget resolves .. lexically when the directory is gone" {
  # _projects_abs can't cd into a directory that no longer exists to resolve ..
  # the normal way — it has to collapse the path components itself.
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/deleted-repo"
  mkdir -p "$TMPDIR/elsewhere"
  printf '%s\n' "$TMPDIR/deleted-repo" > "$PROJECTS_REGISTRY_FILE"
  rm -rf "$TMPDIR/deleted-repo"

  cd "$TMPDIR/elsewhere"
  run "$REPO_ROOT/bin/otto-workbench" projects forget "../deleted-repo"
  [ "$status" -eq 0 ]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "0" ]
}

@test "projects forget without an argument is a usage error" {
  run "$REPO_ROOT/bin/otto-workbench" projects forget
  [ "$status" -eq 1 ]
  [[ "$output" == *"projects forget DIR"* ]]
}

@test "projects forget reports a path that was never registered" {
  run "$REPO_ROOT/bin/otto-workbench" projects forget "$TMPDIR/nowhere"
  [ "$status" -eq 1 ]
  [[ "$output" == *"Not in the registry"* ]]
}

@test "projects prune deletes the entries list was skipping" {
  mkdir -p "$WORKBENCH_STATE_DIR"
  make_repo "$TMPDIR/alpha"
  printf '%s\n%s\n' "$TMPDIR/alpha" "$TMPDIR/gone" > "$PROJECTS_REGISTRY_FILE"

  run "$REPO_ROOT/bin/otto-workbench" projects prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"Pruned 1 entry(s)"* ]]
  run grep -c . "$PROJECTS_REGISTRY_FILE"
  [ "$output" = "1" ]
}

@test "an unknown projects subcommand prints usage and fails" {
  run "$REPO_ROOT/bin/otto-workbench" projects nonsense
  [ "$status" -eq 1 ]
  [[ "$output" == *"Usage: otto-workbench projects"* ]]
}

@test "projects prune reports an orphaned memory directory without a shell error" {
  # memory_orphans (lib/projects.sh) calls _repo_key while walking
  # project_repo_leaders — which only runs it when a repo is actually
  # registered, so a repo needs to be live here for the call to happen at all.
  # _repo_key lives in lib/ai/session-count.sh, which bin/otto-workbench must
  # source itself, or the call fails with "command not found" rather than
  # resolving.
  export WORKBENCH_DATA_DIR="$TMPDIR/data"
  mkdir -p "$WORKBENCH_STATE_DIR" "$WORKBENCH_DATA_DIR/memory/gone-key"
  echo note > "$WORKBENCH_DATA_DIR/memory/gone-key/topic.md"
  make_repo "$TMPDIR/alpha"
  PROJECTS_EXCLUDED_PREFIXES=() run "$REPO_ROOT/bin/otto-workbench" --workbench-dir "$REPO_ROOT" projects add "$TMPDIR/alpha"
  [ "$status" -eq 0 ]

  run "$REPO_ROOT/bin/otto-workbench" projects prune
  [ "$status" -eq 0 ]
  [[ "$output" != *"command not found"* ]]
  [[ "$output" == *"gone-key (1 topic file(s))"* ]]
}

# ─── Consumers ───────────────────────────────────────────────────────────────

@test "the context-to-architecture migration reaches a repo past the old depth limit" {
  # Six levels below the root the old `find -maxdepth 5` walked. A bare-repo
  # container sits at exactly five, so any organisation one directory deeper was
  # invisible — and the migration recorded itself applied all the same. The
  # registry answers by membership rather than by depth, and the migration is
  # handed each repo it lists.
  local deep="$TMPDIR/git/personal/otto-nation/some-repo/main/nested"
  make_repo "$deep"
  mkdir -p "$deep/.claude"
  echo "architecture" > "$deep/.claude/context.md"
  project_register "$deep"

  run project_registered
  [[ "$output" == *"$deep"* ]]

  # The framework as well as the migration: a migration's return codes are the
  # framework's vocabulary, and the twin test below asserts against one of them.
  # shellcheck source=../lib/migrations.sh
  . "$REPO_ROOT/lib/migrations.sh"
  # shellcheck source=../ai/claude/migrations/20260819-context-to-architecture.sh
  . "$REPO_ROOT/ai/claude/migrations/20260819-context-to-architecture.sh"
  run migration_20260819_context_to_architecture "$deep"
  [ "$status" -eq 0 ]

  [ ! -f "$deep/.claude/context.md" ]
  [ "$(cat "$deep/.claude/architecture.md")" = "architecture" ]
}

@test "the context-to-architecture migration leaves an existing architecture.md alone" {
  # No registration here, unlike the test above: what the framework hands the
  # migration is a repo path, and this one is about what the migration does with
  # the files it finds there rather than about how the path was arrived at.
  make_repo "$TMPDIR/alpha"
  mkdir -p "$TMPDIR/alpha/.claude"
  echo "old" > "$TMPDIR/alpha/.claude/context.md"
  echo "current" > "$TMPDIR/alpha/.claude/architecture.md"

  # shellcheck source=../lib/migrations.sh
  . "$REPO_ROOT/lib/migrations.sh"
  # shellcheck source=../ai/claude/migrations/20260819-context-to-architecture.sh
  . "$REPO_ROOT/ai/claude/migrations/20260819-context-to-architecture.sh"
  run migration_20260819_context_to_architecture "$TMPDIR/alpha"
  # MIGRATION_NOOP, not 0: leaving the file alone is the migration finding its
  # work already done, and the framework counts and reports the two apart.
  [ "$status" -eq "$MIGRATION_NOOP" ]
  [ "$(cat "$TMPDIR/alpha/.claude/architecture.md")" = "current" ]
}
