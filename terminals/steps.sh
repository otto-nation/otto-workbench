#!/usr/bin/env bash
# Terminals sync steps — re-applies config for each installed terminal.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

_TERMINALS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source sub-component steps so sync_ghostty is available.
# shellcheck source=terminals/ghostty/steps.sh
. "$_TERMINALS_DIR/ghostty/steps.sh"

# sync_terminals — re-applies config for each installed terminal.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_terminals() {
  if [[ "$OSTYPE" != "darwin"* ]]; then
    echo -e "  ${DIM}⊘ terminal sync is macOS-only${NC}"
    return
  fi
  sync_ghostty
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Terminals sync${NC}\n"
  sync_terminals
  echo
  success "Terminals sync complete!"
fi
