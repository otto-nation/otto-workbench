#!/usr/bin/env bash
# Migration: repair a [credential] helper that names a missing executable.
#
# step_gitconfig runs the same repair, but components sync alphabetically and
# the ai component runs first: its Pi package update clones over HTTPS, and a
# ~/.gitconfig that still names /opt/homebrew/share/gcm-core on a Linux host
# stops that clone at a username prompt before the git step is reached.
# Migrations run ahead of every component, so the repair lands here first.
#
# The repair lives in git/steps.sh (sourced before migrations run); this only
# decides what to record. A helper that later goes missing is still caught by
# step_gitconfig on every sync.

migration_20261007_repair_credential_helper() {
  # No ~/.gitconfig yet: the git component bootstraps one later in this sync
  # with a detected helper, so there is nothing to repair. Deferred rather than
  # recorded, so a gitconfig restored from another machine is still checked.
  [[ -f "$GITCONFIG_FILE" ]] || return "$MIGRATION_DEFERRED"

  local before
  before="$(cat "$GITCONFIG_FILE")"
  _gitconfig_repair_credential_helper
  [[ "$(cat "$GITCONFIG_FILE")" != "$before" ]] || return "$MIGRATION_NOOP"
}
