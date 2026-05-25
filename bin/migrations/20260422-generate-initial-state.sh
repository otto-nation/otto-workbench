#!/usr/bin/env bash
# Migration: generate initial installation state.
# Detects what is currently installed and records it so that
# otto-workbench sync can selectively sync only installed components.

migration_20260422_generate_initial_state() {
  # Skip if YAML state already exists
  [[ -f "$INSTALL_YML_FILE" ]] && return 0
  # Skip if old state file exists (will be migrated by 20260524)
  [[ -f "$INSTALLED_STATE_FILE" ]] && return 0

  info "Generating initial installation state"
  state_detect_installed
  success "Installation state generated"
}
