#!/usr/bin/env bash
# Migration: convert installed.components flat file to install.yml YAML.
# Reads each line from the old state file and calls state_record (which now
# writes YAML). Sub-selections (docker runtime, brew stacks) are populated
# on the next interactive install or via `otto-workbench discover regenerate`.

migration_20260524_migrate_state_to_yaml() {
  # Already in the target shape, and a YAML state file is never converted back.
  [[ -f "$INSTALL_YML_FILE" ]] && return "$MIGRATION_NOOP"
  # Neither file exists, so there is nothing to convert yet. In practice
  # 20260422-generate-initial-state writes install.yml earlier in this same sync
  # — migrations run in filename order — so the guard above answers first and
  # this line is reached only on a machine where that one did not run. Deferring
  # is what keeps such a machine from recording a conversion it never made.
  [[ -f "$INSTALLED_STATE_FILE" ]] || return "$MIGRATION_DEFERRED"

  info "Migrating installation state to YAML"

  local entry
  while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue
    state_record "$entry"
  done < "$INSTALLED_STATE_FILE"

  mv "$INSTALLED_STATE_FILE" "${INSTALLED_STATE_FILE}.migrated"
  success "Installation state migrated to install.yml"
}
