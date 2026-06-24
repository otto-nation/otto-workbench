#!/usr/bin/env bash
# Homebrew preflight — ensures brew is installed before components that need it.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_brew_install — prompts to install Homebrew if not already present.
# macOS-only; no-op on Linux. This is an install-time step — not called by sync.
step_brew_install() {
  # Homebrew is macOS-only — skip entirely on Linux.
  [[ "$OSTYPE" == "darwin"* ]] || return 0

  if command -v brew >/dev/null 2>&1; then
    success "brew already installed"
    return
  fi

  warn "Homebrew is not installed"

  if [[ ! -t 0 ]]; then
    warn "Non-interactive shell — skipping brew install. Run otto-workbench install manually to install Homebrew."
    return
  fi

  confirm "  Install Homebrew?" || return

  info "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  success "Homebrew installed"
}

# sync_brew — no-op. Brew packages are installed interactively via setup.sh;
# there is nothing to reconcile on sync (brew bundle is not re-run automatically).
sync_brew() { :; }

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Homebrew setup${NC}\n"
  step_brew_install
  echo
  success "Homebrew setup complete!"
fi
