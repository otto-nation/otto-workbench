#!/usr/bin/env bash
# Mise setup — installs mise directly (not via brew) for version control.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_mise_install — prompts to install mise if not already present.
# Uses the official mise installer (https://mise.run) which installs to ~/.local/bin.
# No-op if mise is already installed. This is an install-time step — not called by sync_mise.
step_mise_install() {
  command -v mise >/dev/null 2>&1 && return
  warn "mise (version manager) is not installed"
  if [[ ! -t 0 ]]; then
    warn "Non-interactive shell — skipping mise install. Run install.sh manually to install mise."
    return
  fi
  confirm "  Install mise?" || return

  info "Installing mise via official installer..."
  curl -fsSL https://mise.run | sh
}

# sync_mise — no-op; mise activation is handled by zsh/config.d/tools/mise.zsh.
sync_mise() {
  :
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Mise setup${NC}\n"

  echo; info "Mise version manager"
  step_mise_install

  sync_mise

  echo
  success "Mise setup complete!"
fi
