#!/usr/bin/env bash
# Migration: generate initial installed.components state file.
# Detects what is currently installed and records it so that
# otto-workbench sync can selectively sync only installed components.

migration_20260422_generate_initial_state() {
  # Skip if state file already exists (already migrated or fresh install)
  state_file_exists && return 0

  info "Generating initial installation state"

  # Core components — always present in a workbench install
  state_record "bin"
  state_record "git"
  state_record "zsh"

  # Docker — detect by state symlink presence
  if [[ -L "$DOCKER_RUNTIME_ALIASES" ]]; then
    state_record "docker"
  fi

  # AI / Claude — detect by settings file
  if [[ -f "$CLAUDE_SETTINGS_FILE" ]]; then
    state_record "ai"
    state_record "ai/claude"
  fi

  # AI / Serena — detect by serena-mcp symlink in ~/.local/bin
  if [[ -L "$LOCAL_BIN_DIR/serena-mcp" ]]; then
    state_record "ai"
    state_record "ai/serena"
  fi

  # Terminals / Ghostty — detect by config directory
  if [[ -d "$GHOSTTY_CONFIG_DIR" ]]; then
    state_record "terminals"
    state_record "terminals/ghostty"
  fi

  # Editors / Zed — detect by settings file
  if [[ -f "$ZED_SETTINGS_FILE" ]]; then
    state_record "editors"
    state_record "editors/zed"
  fi

  # Editors / Sublime — detect by settings file
  if [[ -f "$SUBLIME_SETTINGS_FILE" ]]; then
    state_record "editors"
    state_record "editors/sublime"
  fi

  success "Installation state generated"
}
