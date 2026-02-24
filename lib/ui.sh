#!/bin/bash
# Shared UI helpers — sourced by all workbench scripts
#
# Sourcing patterns:
#   install.sh        . "$DOTFILES_DIR/lib/ui.sh"
#   ai/setup.sh       . "$SCRIPT_DIR/../lib/ui.sh"
#   bin/* (bash)      _SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"; . "$(dirname "$_SELF")/../lib/ui.sh"
#   bin/* (zsh)       _SELF="$(readlink "$0" 2>/dev/null || echo "$0")"; . "$(dirname "$_SELF")/../lib/ui.sh"

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
fi
