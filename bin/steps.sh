#!/bin/bash
# Bin scripts setup — symlinks workbench scripts to ~/.local/bin/.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_bin — symlinks executable scripts from BIN_SRC_DIR into LOCAL_BIN_DIR.
# Uses extglob to match only extensionless files, skipping registry.yml, steps.sh, etc.
# --prune removes stale symlinks pointing into BIN_SRC_DIR that no longer have a source.
step_bin() {
  mkdir -p "$LOCAL_BIN_DIR"
  shopt -s extglob
  symlink_dir "$BIN_SRC_DIR" "$LOCAL_BIN_DIR" "!(*.*)" --prune
  shopt -u extglob
}

# sync_bin — re-symlinks all bin scripts; safe to run non-interactively.
sync_bin() {
  echo; info "bin scripts → $LOCAL_BIN_DIR/"
  step_bin
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Bin setup${NC}\n"

  sync_bin

  echo
  success "Bin setup complete!"
fi
