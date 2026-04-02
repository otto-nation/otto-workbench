#!/bin/bash
# Ghostty setup — sourced by terminals/setup.sh, do not run directly.
#
# Installs the Ghostty cask if absent, then bootstraps ~/.config/ghostty/config
# from the workbench template (non-destructive: skips if config already exists).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../../lib/ui.sh"
# shellcheck source=terminals/ghostty/steps.sh
. "$SCRIPT_DIR/steps.sh"

echo; info "Ghostty"

step_ghostty_install
echo
info "Config ($GHOSTTY_CONFIG_FILE):"
step_ghostty_config
echo
info "Theme:"
step_ghostty_theme
echo
info "Migrations:"
run_migrations "$GHOSTTY_SRC_DIR"

echo
# shellcheck source=terminals/ghostty/summary.sh
. "$SCRIPT_DIR/summary.sh"
print_ghostty_summary
