#!/bin/bash
# Ghostty config migrations — sourced by run_migrations in sync_ghostty.
# All patches are idempotent: no-op if the old value is not present.
# Add new patches at the bottom; do not remove old ones.

# Theme name corrected to match Ghostty's built-in name (space-separated)
apply_config_patch "$GHOSTTY_CONFIG_FILE" 'theme = GruvboxDark' 'theme = Gruvbox Dark'
