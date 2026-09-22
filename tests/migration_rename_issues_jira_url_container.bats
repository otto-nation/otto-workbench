#!/usr/bin/env bats
# Tests for bin/migrations/20260922-rename-issues-jira-url-container.sh —
# renames issues.jira_url to issues.base_url in the .workbench.yml beside a bare
# repo's worktrees.
#
# Its own file rather than a block in migrations.bats, for the reason
# migration_rename_issue_tracker_container.bats is: this migration shells out to
# bin/resolve-worktree, and migrations.bats builds a fake workbench whose
# BIN_SRC_DIR holds nothing. That fixture is right for testing the framework and
# wrong for testing a migration that reaches a real script.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/bin/migrations/20260922-rename-issues-jira-url-container.sh"
  SEED="$TMPDIR/seed"
  mkdir -p "$SEED"
  printf 'x\n' > "$SEED/a.txt"
  make_container_seed "$SEED"
}

teardown() {
  common_teardown
}

# Runs the migration against PROJECT_DIR with the ui.sh helpers stubbed out, as
# the companion container suite does — the same libs reach this migration from
# the framework's sourcing environment, so none is redefined.
_run_migration() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$3"
    LIB_SRC_DIR="$3/lib"
    BIN_SRC_DIR="$3/bin"
    LEGACY_WORKBENCH_ROOT="$4/.unused-legacy"
    WORKBENCH_PROJECT_CONFIG_NAME=".workbench.yml"
    . "$WORKBENCH_DIR/lib/gitenv.sh"
    . "$WORKBENCH_DIR/lib/git_layout.sh"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20260922_rename_issues_jira_url_container "$2"
  ' _ "$MIGRATION" "$1" "$REPO_ROOT" "$TMPDIR"
}

# _seed_legacy DIR [BODY] — a .workbench.yml at DIR holding the old key.
_seed_legacy() {
  printf '%s' "${2:-$(printf 'issues:\n  provider: jira\n  jira_url: https://j.example\n')}" \
    > "$1/.workbench.yml"
}

@test "renames the key in a bare-repo container's config" {
  make_worktree_container "$TMPDIR/c" "$SEED"
  _seed_legacy "$TMPDIR/c"

  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Renamed issues.jira_url to issues.base_url"* ]]
  [ "$(yq -r '.issues.base_url' "$TMPDIR/c/.workbench.yml")" = "https://j.example" ]
  [ "$(yq -r '.issues.jira_url // "absent"' "$TMPDIR/c/.workbench.yml")" = "absent" ]
  [ "$(yq -r '.issues.provider' "$TMPDIR/c/.workbench.yml")" = "jira" ]
}

@test "leaves a worktree's own committed config alone" {
  # The whole reason the repo scope is reported rather than rewritten: that file
  # is tracked, and a rewrite would put an uncommitted change in the checkout.
  make_worktree_container "$TMPDIR/c" "$SEED"
  _seed_legacy "$TMPDIR/c"
  _seed_legacy "$TMPDIR/c/main"

  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issues.jira_url' "$TMPDIR/c/main/.workbench.yml")" = "https://j.example" ]
  [ "$(yq -r '.issues.base_url // "absent"' "$TMPDIR/c/main/.workbench.yml")" = "absent" ]
}

@test "keeps a value already written against the new schema" {
  make_worktree_container "$TMPDIR/c" "$SEED"
  _seed_legacy "$TMPDIR/c" \
    "$(printf 'issues:\n  jira_url: https://old.example\n  base_url: https://new.example\n')"

  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issues.base_url' "$TMPDIR/c/.workbench.yml")" = "https://new.example" ]
  [ "$(yq -r '.issues.jira_url // "absent"' "$TMPDIR/c/.workbench.yml")" = "absent" ]
}

@test "preserves the schema modeline and hand-written comments" {
  make_worktree_container "$TMPDIR/c" "$SEED"
  _seed_legacy "$TMPDIR/c" "$(printf '# yaml-language-server: $schema=https://example/config.schema.json\n# our Jira tenant\nissues:\n  jira_url: https://j.example\n')"

  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  run head -1 "$TMPDIR/c/.workbench.yml"
  [[ "$output" == "# yaml-language-server: \$schema="* ]]
  grep -q "# our Jira tenant" "$TMPDIR/c/.workbench.yml"
}

@test "a second run is a no-op" {
  make_worktree_container "$TMPDIR/c" "$SEED"
  _seed_legacy "$TMPDIR/c"

  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 3 ]
  [ "$(yq -r '.issues.base_url' "$TMPDIR/c/.workbench.yml")" = "https://j.example" ]
}

@test "a container with no config at all is a no-op" {
  make_worktree_container "$TMPDIR/c" "$SEED"

  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 3 ]
  [ ! -e "$TMPDIR/c/.workbench.yml" ]
}

@test "a container whose default branch has no worktree is still renamed" {
  # resolve-worktree answers 1 here rather than 0 — the container holds the
  # config either way, so 1 must not be read as "not a container".
  local container="$TMPDIR/c"
  make_empty_container "$container" "$SEED"
  git -C "$container" worktree add -q "$container/feat" feat
  _seed_legacy "$container"

  run _run_migration "$container/feat"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issues.base_url' "$container/.workbench.yml")" = "https://j.example" ]
}

@test "an ordinary repo keeps its own tracked config" {
  # dirname of a non-bare repo's --git-common-dir is the repo itself, so a
  # migration that skipped the bare check would rewrite a real checkout's
  # committed .workbench.yml — the one file this must never touch.
  local repo="$TMPDIR/plain"
  mkdir -p "$repo"
  git -C "$repo" init -q
  _seed_legacy "$repo"

  run _run_migration "$repo"
  [ "$status" -eq 3 ]
  [ "$(yq -r '.issues.jira_url' "$repo/.workbench.yml")" = "https://j.example" ]
}
