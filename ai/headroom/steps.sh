#!/usr/bin/env bash
# description: Headroom token compression tool
# Headroom setup steps — sourced by ai/setup.sh.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

# sync_headroom — verifies headroom is installed.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
sync_headroom() {
  sync_header "headroom"
  if command -v headroom >/dev/null 2>&1; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "headroom installed" || true
  else
    warn "headroom not found — run: pipx install 'headroom-ai[all]'"
  fi
}

# step_install_headroom — installs headroom via pipx if not already in PATH.
step_install_headroom() {
  if command -v headroom >/dev/null 2>&1; then
    success "headroom already installed"
    return
  fi

  command -v pipx >/dev/null 2>&1 || {
    warn "pipx not found — run: brew install pipx"
    return
  }

  info "Installing headroom via pipx"
  pipx install "headroom-ai[all]"
  success "headroom installed"
}

register_headroom_steps() {
  register_step "Install headroom" step_install_headroom
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Headroom sync${NC}\n"
  sync_headroom
  echo
  success "Headroom sync complete!"
fi
