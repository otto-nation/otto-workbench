#!/usr/bin/env bash
# Sublime Text setup — sourced by editors/setup.sh, do not run directly.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../../lib/ui.sh"
# shellcheck source=editors/sublime/steps.sh
. "$SCRIPT_DIR/steps.sh"

echo; info "Sublime Text"

if [[ ! -d "$SUBLIME_PREFS_DIR" ]]; then
  warn "Sublime Text not installed — Packages/User dir not found"
  info "Open Sublime Text once to initialize it, then re-run: bash editors/setup.sh"
  return
fi

info "Preferences ($SUBLIME_SETTINGS_FILE):"
step_sublime_settings
