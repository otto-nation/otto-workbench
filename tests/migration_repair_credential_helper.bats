#!/usr/bin/env bats
# Tests for git/migrations/20261007-repair-credential-helper.sh — repairs a
# missing [credential] helper before any component sync can clone over HTTPS.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/git/migrations/20261007-repair-credential-helper.sh"
  GITCONFIG="$BATS_TEST_TMPDIR/.gitconfig"
}

teardown() {
  common_teardown
}

# Runs the migration the way lib/migrations.sh does — git/steps.sh already
# sourced (bin/otto-workbench sources every steps.sh before migrations), then
# the migration file, then its function — with the helper detector pinned.
_run_migration() {
  bash -c '
    . "$3/lib/ui.sh"
    . "$3/git/steps.sh"
    _git_detect_credential_helper() { echo "!/usr/bin/gh auth git-credential"; }
    MIGRATION_NOOP=3
    MIGRATION_DEFERRED=4
    GITCONFIG_FILE="$2"
    . "$1"
    migration_20261007_repair_credential_helper
  ' _ "$MIGRATION" "$GITCONFIG" "$REPO_ROOT"
}

@test "replaces a helper path that does not exist" {
  printf '[credential]\n\thelper =\n\thelper = /opt/homebrew/share/gcm-core/git-credential-manager-missing\n' > "$GITCONFIG"

  run _run_migration

  [ "$status" -eq 0 ]
  run git config --file "$GITCONFIG" --get-all credential.helper
  [ "$output" = "$(printf '\n!/usr/bin/gh auth git-credential')" ]
}

@test "reports NOOP when every helper runs" {
  printf '[credential]\n\thelper =\n\thelper = !/usr/bin/gh auth git-credential\n' > "$GITCONFIG"
  cp "$GITCONFIG" "$BATS_TEST_TMPDIR/before"

  run _run_migration

  [ "$status" -eq 3 ]
  cmp "$BATS_TEST_TMPDIR/before" "$GITCONFIG"
}

@test "defers when there is no gitconfig yet" {
  run _run_migration

  [ "$status" -eq 4 ]
  [ ! -e "$GITCONFIG" ]
}
