#!/usr/bin/env bash
# Migration: correct Ghostty theme name to match built-in name (space-separated).
# Idempotent — no-op if the old value is not present.

migration_20240601_theme_name_fix() {
  apply_config_patch "$GHOSTTY_CONFIG_FILE" 'theme = GruvboxDark' 'theme = Gruvbox Dark'
}
