#!/usr/bin/env bash
# description: Symlink AI scripts to ~/.local/bin
# AI component sync — sub-tools handle their own script installation.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# sync_ai — no-op; sub-tools (claude, serena) handle their own script installation.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_ai() {
  # No component-level scripts — sub-tools (claude, serena) handle their own.
  :
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}AI bin setup${NC}\n"

  sync_ai

  echo
  success "AI bin setup complete!"
fi
