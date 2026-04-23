#!/usr/bin/env bash
# Migration: generate initial installed.components state file.
# Detects what is currently installed and records it so that
# otto-workbench sync can selectively sync only installed components.

migration_20260422_generate_initial_state() {
  # Skip if state file already exists (already migrated or fresh install)
  state_file_exists && return 0

  info "Generating initial installation state"
  state_detect_installed
  success "Installation state generated"
}
