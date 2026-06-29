#!/usr/bin/env bash
# description: RTK token compression tool
# RTK setup steps — sourced by ai/setup.sh.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

# sync_rtk — verifies rtk is installed and hook is active.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
sync_rtk() {
  sync_header "rtk"
  if ! command -v rtk >/dev/null 2>&1; then
    warn "rtk not found — run: brew install rtk"
    return
  fi
  [[ "${WORKBENCH_SYNC:-}" != true ]] && success "rtk installed" || true
}

# step_install_rtk — installs rtk via brew if not already in PATH.
step_install_rtk() {
  if command -v rtk >/dev/null 2>&1; then
    success "rtk already installed"
    return
  fi

  command -v brew >/dev/null 2>&1 || {
    warn "brew not found — install Homebrew first"
    return
  }

  info "Installing rtk via brew"
  brew install rtk
  success "rtk installed"
}

# step_rtk_hook — installs the RTK PreToolUse hook into ~/.claude/settings.json.
# Uses --hook-only to avoid mutating CLAUDE.md (managed by the workbench).
step_rtk_hook() {
  if ! command -v rtk >/dev/null 2>&1; then
    warn "rtk not installed — skipping hook setup"
    return
  fi

  rtk init -g --hook-only --auto-patch
  success "rtk hook configured"
}

register_rtk_steps() {
  register_step "Install rtk" step_install_rtk
  register_step "Configure rtk hook" step_rtk_hook
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}RTK sync${NC}\n"
  sync_rtk
  echo
  success "RTK sync complete!"
fi
