#!/usr/bin/env bash
# description: Serena MCP scaffolding tool
# Serena setup steps — sourced by ai/setup.sh.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../../lib/ui.sh"
  unset _D
fi

# sync_serena — symlinks serena scripts to ~/.local/bin/.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
sync_serena() {
  echo; info "serena scripts → $LOCAL_BIN_DIR/"
  sync_component_bin "$SERENA_SRC_DIR"
}

register_serena_steps() {
  : # No interactive steps — serena-mcp is a standalone script
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Serena sync${NC}\n"
  sync_serena
  echo
  success "Serena sync complete!"
fi
