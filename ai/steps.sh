#!/usr/bin/env bash
# description: Symlink AI scripts to ~/.local/bin
# AI bin scripts setup — symlinks ai/bin/ scripts to ~/.local/bin/.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# sync_ai — symlinks ai/bin/ scripts to ~/.local/bin/.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_ai() {
  echo; info "ai scripts → $LOCAL_BIN_DIR/"
  sync_component_bin "$AI_SRC_DIR"
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}AI bin setup${NC}\n"

  sync_ai

  echo
  success "AI bin setup complete!"
fi
