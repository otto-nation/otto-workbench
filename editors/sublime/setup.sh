#!/usr/bin/env bash
# Sublime Text setup — sourced by editors/setup.sh, do not run directly.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../../lib/ui.sh"
# shellcheck source=editors/sublime/steps.sh
. "$SCRIPT_DIR/steps.sh"

echo; info "Sublime Text"

if [[ ! -d "$SUBLIME_PREFS_DIR" ]]; then
  skip "Sublime Text not installed — open it once to initialize, then re-run: bash editors/setup.sh"
  return
fi

info "Preferences ($SUBLIME_SETTINGS_FILE):"
step_sublime_settings
