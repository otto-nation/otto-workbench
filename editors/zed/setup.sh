#!/usr/bin/env bash
# Zed setup — sourced by editors/setup.sh, do not run directly.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../../lib/ui.sh"
# shellcheck source=editors/zed/steps.sh
. "$SCRIPT_DIR/steps.sh"

echo; info "Zed"

if [[ ! -d "$ZED_CONFIG_DIR" ]]; then
  warn "Zed config dir not found at $ZED_CONFIG_DIR"
  info "Open Zed once to initialize it, then re-run: bash editors/setup.sh"
  return
fi

info "Settings ($ZED_SETTINGS_FILE):"
step_zed_settings
