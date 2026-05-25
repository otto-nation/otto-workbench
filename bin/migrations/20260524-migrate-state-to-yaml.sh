#!/usr/bin/env bash
# Migration: convert installed.components flat file to install.yml YAML.
# Reads each line from the old state file and calls state_record (which now
# writes YAML). Enriches docker runtime from the existing symlink target.

migration_20260524_migrate_state_to_yaml() {
  [[ -f "$INSTALL_YML_FILE" ]] && return 0
  [[ -f "$INSTALLED_STATE_FILE" ]] || return 0

  info "Migrating installation state to YAML"

  local entry
  while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue
    state_record "$entry"
  done < "$INSTALLED_STATE_FILE"

  mv "$INSTALLED_STATE_FILE" "${INSTALLED_STATE_FILE}.migrated"
  success "Installation state migrated to install.yml"
}
