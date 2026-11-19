#!/usr/bin/env bash
# Mise setup — installs mise directly (not via brew) for version control.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_mise_install — prompts to install mise if not already present.
# Uses the official mise installer (https://mise.run, from $MISE_INSTALL_URL) which installs to ~/.local/bin.
# No-op if mise is already installed. This is an install-time step — not called by sync_mise.
step_mise_install() {
  command -v mise >/dev/null 2>&1 && return
  skip "mise (version manager) is not installed"
  if [[ ! -t 0 ]]; then
    warn "Non-interactive shell — skipping mise install. Run otto-workbench install manually to install mise."
    return
  fi
  confirm "  Install mise?" || return

  info "Installing mise via official installer..."
  curl -fsSL "$MISE_INSTALL_URL" | sh
}

# _mise_is_path_wrapper FILE — true when FILE is the workbench path wrapper.
_mise_is_path_wrapper() {
  local start
  start="$(dd if="$1" bs=1024 count=1 2>/dev/null | tr -d '\0')" || true
  [[ "$start" == *mise-path-wrapper* ]]
}

# sync_mise — put the path wrapper at ~/.local/bin/mise and the real binary at
# MISE_REAL_BIN. Shims symlink to ~/.local/bin/mise, so the wrapper is what
# every mise invocation and every shim runs. Idempotent; relocates a binary
# that mise.run (or an upgrade) wrote over the wrapper.
sync_mise() {
  local front="$LOCAL_BIN_DIR/mise"
  local real="$MISE_REAL_BIN"
  [[ -e "$front" || -L "$front" ]] || return 0

  mkdir -p "$(dirname "$real")"
  if ! _mise_is_path_wrapper "$front"; then
    rm -f "$real"
    mv "$front" "$real"
    chmod +x "$real" || true
  fi
  if [[ ! -x "$real" ]]; then
    warn "mise wrapper: no binary at $real — skip"
    return 0
  fi
  install_file "$MISE_WRAPPER_SRC" "$front" "mise path wrapper"
  chmod +x "$front"
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
