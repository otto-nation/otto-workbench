#!/bin/bash
# Homebrew preflight — ensures brew is installed before components that need it.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_brew_install — prompts to install Homebrew if not already present.
# macOS: runs the official Homebrew installer. Linux: prints manual instructions.
# No-op if brew is already installed. This is an install-time step — not called by sync.
step_brew_install() {
  if command -v brew >/dev/null 2>&1; then
    success "brew already installed"
    return
  fi

  warn "Homebrew is not installed"

  if [[ ! -t 0 ]]; then
    warn "Non-interactive shell — skipping brew install. Run install.sh manually to install Homebrew."
    return
  fi

  if [[ "$OSTYPE" == "darwin"* ]]; then
    printf "  Install Homebrew? [Y/n] "
    read -n 1 -r REPLY
    echo
    [[ "$REPLY" =~ ^[Nn]$ ]] && return

    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    success "Homebrew installed"
  else
    err "Homebrew auto-install is macOS-only. See: https://brew.sh"
  fi
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
