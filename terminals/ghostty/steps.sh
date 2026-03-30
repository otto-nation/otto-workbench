#!/bin/bash
# Ghostty sync steps — installs Ghostty and bootstraps config.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../../lib/ui.sh"
  unset _D
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_ghostty_install — installs the Ghostty cask if not already present.
step_ghostty_install() {
  if command -v ghostty >/dev/null 2>&1; then
    echo -e "  ${DIM}✓ ghostty already installed${NC}"
    return
  fi
  require_command brew "Homebrew not found — install Ghostty manually: https://ghostty.org" || return
  info "Installing Ghostty..."
  brew install --cask ghostty
  success "Ghostty installed"
}

# step_ghostty_config — creates ~/.config/ghostty/config from template if absent.
# Non-destructive: skips if the file already exists.
step_ghostty_config() {
  mkdir -p "$GHOSTTY_CONFIG_DIR"
  if [[ -f "$GHOSTTY_CONFIG_FILE" ]]; then
    echo -e "  ${DIM}✓ $GHOSTTY_CONFIG_FILE already exists — skipping${NC}"
    return
  fi
  cp "$GHOSTTY_CONFIG_TEMPLATE" "$GHOSTTY_CONFIG_FILE"
  success "Created $GHOSTTY_CONFIG_FILE from template"
}

# sync_ghostty — re-applies Ghostty config and runs migrations if Ghostty is installed.
# Called by sync_terminals() in terminals/steps.sh.
sync_ghostty() {
  if ! command -v ghostty >/dev/null 2>&1 && [[ ! -d "$GHOSTTY_CONFIG_DIR" ]]; then
    echo -e "  ${DIM}⊘ ghostty not installed — skipping${NC}"
    return
  fi
  echo; info "Ghostty config ($GHOSTTY_CONFIG_FILE)"
  step_ghostty_config
  echo; info "Ghostty migrations"
  run_migrations "$GHOSTTY_SRC_DIR"
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Ghostty sync${NC}\n"
  sync_ghostty
  echo
  success "Ghostty sync complete!"
fi
