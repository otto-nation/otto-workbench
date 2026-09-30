#!/usr/bin/env bats
# Tests for the memory-to-data-root migration.
#
# The migration relies on _encode_slug, _repo_key, _gate_repo_dir and
# _gate_stamp_file, all defined in lib/ai/session-count.sh. Nothing else on the
# migration framework's load path pulls that file in, so a run that skips
# sourcing it fails closed: every slug is reported as an unresolvable orphan
# and the memory tree is never carried.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  export HOME="$TMPDIR/home"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_DATA_DIR="$TMPDIR/data"
  export PROJECTS_REGISTRY_FILE="$TMPDIR/state/projects.registry"
  mkdir -p "$HOME/.claude/projects" "$TMPDIR/state" "$TMPDIR/data"

  REPO_DIR="$TMPDIR/repo"
  mkdir -p "$REPO_DIR"
  git -C "$REPO_DIR" init --quiet
  git -C "$REPO_DIR" -c user.email=t@example.com -c user.name=t commit --allow-empty -qm init

  # The slug Claude's own transform gives this repo's path — the migration
  # looks up the registry to resolve a directory named this way back to
  # $REPO_DIR.
  SLUG="$(bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/ai/session-count.sh'
    _encode_slug '$REPO_DIR' 'A-Za-z0-9'
  ")"

  MEM_DIR="$HOME/.claude/projects/$SLUG/memory"
  mkdir -p "$MEM_DIR"
  echo "some note" > "$MEM_DIR/topic.md"

  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260930-memory-to-data-root.sh"
}

teardown() {
  common_teardown
}

# _run_migration — sources the migration the way lib/migrations.sh does
# (lib/ui.sh, then lib/migrations.sh for MIGRATION_NOOP, then the migration
# file) and calls its function, registering the repo first so the registry
# lookup the migration depends on has something to resolve.
_run_migration() {
  WORKBENCH_DIR="$REPO_ROOT" run bash -c "
    # The default exclusion list refuses anything under /tmp or
    # /var/folders, which is exactly where the bats sandbox lives.
    PROJECTS_EXCLUDED_PREFIXES=('$TMPDIR/state' '$TMPDIR/data')
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    project_register '$REPO_DIR'
    record_project_repo_ids
    . '$MIGRATION'
    migration_20260930_memory_to_data_root
  "
}

@test "carries a registered repo's memory to the data root, keyed by repo" {
  _run_migration
  [ "$status" -eq 0 ]

  key="$(bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/ai/session-count.sh'
    _repo_key '$REPO_DIR'
  ")"

  [ -f "$WORKBENCH_DATA_DIR/memory/$key/topic.md" ]
  [ "$(cat "$WORKBENCH_DATA_DIR/memory/$key/topic.md")" = "some note" ]
  # renamed rather than deleted, and the topic file came with it: memory is
  # authored and has no producer to write it again, so the migration leaves
  # the source for a person to delete once they have looked.
  #
  # Counted through a glob expansion rather than `[ -d ... ]` on the pattern:
  # `-d` takes one operand, so against a pattern it tests the literal string
  # when nothing matches and silently passes on whichever path sorts first
  # when several do.
  local migrated=("$HOME/.claude/projects/$SLUG"/memory-migrated-*)
  [ "${#migrated[@]}" -eq 1 ]
  [ -d "${migrated[0]}" ]
  [ -f "${migrated[0]}/topic.md" ]
  [ ! -d "$MEM_DIR" ]
}

@test "does not report a registered repo's memory as orphaned" {
  _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" != *"Could not resolve a repo"* ]]
}

@test "removes an unresolvable empty memory directory instead of orphaning it" {
  # A slug no registry entry and no transcript can resolve, holding nothing.
  # Before the empty check it was reported as an orphan and the migration
  # returned non-zero, so every later sync retried it and re-warned forever
  # over memory that does not exist.
  local stray="$HOME/.claude/projects/-private-tmp"
  mkdir -p "$stray/memory"

  _run_migration

  [ "$status" -eq 0 ]
  [[ "$output" != *"Could not resolve a repo"* ]]
  [ ! -d "$stray/memory" ]
}

@test "reports and retries an empty directory it cannot remove" {
  # A refused rmdir leaves the directory on disk. Swallowed, a run that
  # visited only this one returns MIGRATION_NOOP, which the framework records
  # as applied and never retries — the directory then survives with nothing
  # left to look at it.
  local stray="$HOME/.claude/projects/-private-tmp"
  mkdir -p "$stray/memory"
  chmod a-w "$stray"

  _run_migration
  chmod u+w "$stray"

  [ "$status" -ne 0 ]
  [[ "$output" == *"Could not remove empty"* ]]
  [ -d "$stray/memory" ]
}

@test "lists a dotfile when reporting an unresolvable directory" {
  # The listing globs through the dotfile-aware helper, so a directory holding
  # only stamps is reported with what it holds rather than as holding nothing.
  local stray="$HOME/.claude/projects/-private-tmp"
  mkdir -p "$stray/memory"
  echo 1700000000 > "$stray/memory/.last-dream"

  _run_migration

  [[ "$output" == *"Could not resolve a repo"* ]]
  [[ "$output" == *"holds .last-dream"* ]]
}

@test "reads an empty directory as empty under a caller's nullglob" {
  # With nullglob already on, a glob over an empty directory yields zero
  # elements rather than the literal pattern. A helper testing for the
  # unexpanded pattern reads that as non-empty and the directory is orphaned.
  local stray="$HOME/.claude/projects/-private-tmp"
  mkdir -p "$stray/memory"

  WORKBENCH_DIR="$REPO_ROOT" run bash -c "
    shopt -s nullglob
    PROJECTS_EXCLUDED_PREFIXES=('$TMPDIR/state' '$TMPDIR/data')
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    project_register '$REPO_DIR'
    record_project_repo_ids
    . '$MIGRATION'
    migration_20260930_memory_to_data_root
  "

  [ "$status" -eq 0 ]
  [[ "$output" != *"Could not resolve a repo"* ]]
  [ ! -d "$stray/memory" ]
}

@test "a directory holding only gate stamps is not empty and is carried" {
  # The stamps are dotfiles, so a bare glob reads this directory as empty and
  # the run would rmdir it (or fail to, and miscount) rather than carrying the
  # cooldown across.
  rm "$MEM_DIR/topic.md"
  echo 1700000000 > "$MEM_DIR/.last-dream"

  _run_migration
  [ "$status" -eq 0 ]

  # Renamed by the carry path, which is what says it was not taken as empty.
  local migrated=("$HOME/.claude/projects/$SLUG"/memory-migrated-*)
  [ "${#migrated[@]}" -eq 1 ]
  [ -f "${migrated[0]}/.last-dream" ]
  [ ! -d "$MEM_DIR" ]
}
