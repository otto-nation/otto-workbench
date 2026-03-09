#!/bin/bash
# Shared UI helpers — sourced by all workbench scripts
#
# Sourcing patterns:
#   install.sh        . "$DOTFILES_DIR/lib/ui.sh"
#   ai/setup.sh       . "$SCRIPT_DIR/../lib/ui.sh"
#   bin/* (bash)      _SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"; . "$(dirname "$_SELF")/../lib/ui.sh"
#   bin/* (zsh)       _SELF="$(readlink "$0" 2>/dev/null || echo "$0")"; . "$(dirname "$_SELF")/../lib/ui.sh"

# shellcheck disable=SC2034  # All color variables are used by sourcing scripts
BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

info()    { echo -e "${BLUE}→${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
err()     { echo -e "${RED}✗${NC} $*" >&2; }

# skip [label] — print a skip line with optional label
skip() { echo -e "${DIM}⊘ ${1:-Skipped}${NC}"; }

# Prompt helpers — bash only
# read -n 1 behaves differently in zsh; these are skipped silently when sourced from a zsh script
if [[ -n "${BASH_VERSION:-}" ]]; then
  # confirm "msg" — [Y/n]; returns 0 for yes (default), 1 for no
  confirm() {
    local msg=$1
    read -r -n 1 -p "$msg [Y/n] " REPLY
    echo
    [[ ! $REPLY =~ ^[Nn]$ ]]
  }

  # confirm_n "msg" — [y/N]; returns 0 for yes, 1 for no (default)
  confirm_n() {
    local msg=$1
    read -r -n 1 -p "$msg [y/N] " REPLY
    echo
    [[ $REPLY =~ ^[Yy]$ ]]
  }

  # prompt_overwrite FILE — warns that FILE already exists and asks whether to overwrite it.
  # Offers an optional backup step before overwriting. Returns 1 (skip) if the user declines.
  prompt_overwrite() {
    local file=$1
    warn "$file already exists"
    printf "  Overwrite? [y/N] "
    read -n 1 -r REPLY
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then return 1; fi

    printf "  Create backup? [Y/n] "
    read -n 1 -r REPLY
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
      cp "$file" "${file}.backup"
      echo -e "  ${GREEN}✓${NC} Backed up to ${file}.backup"
    fi
  }

  # install_symlink SOURCE TARGET — creates or updates a symlink at TARGET pointing to SOURCE.
  # Real files at TARGET trigger prompt_overwrite; existing symlinks are silently replaced
  # (they were almost certainly left by a previous run of this script).
  install_symlink() {
    local source=$1 target=$2 name
    name=$(basename "$source")

    # Only prompt if target is a real file — existing symlinks are silently updated since
    # they were almost certainly installed by a previous run of this script
    if [ -e "$target" ] && [ ! -L "$target" ]; then
      prompt_overwrite "$target" || { echo -e "  ${DIM}⊘ Skipped $name${NC}"; return; }
    fi

    # -h prevents BSD ln from following an existing symlink at $target (macOS default behaviour
    # would dereference it, corrupting repo files or creating nested symlinks on re-runs)
    ln -sfh "$source" "$target"
    echo -e "  ${GREEN}✓${NC} $name"
  }
fi
