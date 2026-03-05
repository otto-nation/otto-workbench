#!/bin/bash
# iTerm2 setup — imports Gruvbox color schemes and prints font configuration instructions.
#
# Color schemes are imported by passing the .itermcolors files to `open`, which registers
# them in iTerm2's color preset library. After running this script, select a preset in:
#   iTerm2 → Settings → Profiles → Colors → Color Presets
#
# Font: Fira Code Nerd Font must be installed first (it is in brew/Brewfile).
# Set it manually in:
#   iTerm2 → Settings → Profiles → Text → Font → "FiraCodeNFM-Reg"
#
# Usage: bash iterm/setup.sh
# Sourced by install.sh; can also be run standalone.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/ui.sh"

if [[ "$OSTYPE" != "darwin"* ]]; then
  warn "iTerm2 setup is macOS-only — skipping"
  exit 0
fi

# _import_theme FILE — registers a .itermcolors file as an iTerm2 color preset.
# `open` hands the file to iTerm2 via its registered file-type handler; iTerm2
# adds it to the Color Presets list without opening a new window.
_import_theme() {
  local file="$1"
  local name
  name=$(basename "$file" .itermcolors)
  if open "$file" 2>/dev/null; then
    success "Imported color preset: $name"
  else
    warn "Could not import $name — is iTerm2 installed?"
  fi
}

echo
info "iTerm2 color schemes:"
_import_theme "$SCRIPT_DIR/themes/gruvbox-dark.itermcolors"
_import_theme "$SCRIPT_DIR/themes/gruvbox-light.itermcolors"

echo
info "Font setup (manual step required):"
echo -e "  ${DIM}1. Open iTerm2 → Settings → Profiles → Text → Font${NC}"
echo -e "  ${DIM}2. Set font to: FiraCodeNFM-Reg  (size 13 recommended)${NC}"
echo -e "  ${DIM}3. Enable: Use ligatures${NC}"
echo
echo -e "  ${DIM}Install font first if not already done: brew install --cask font-fira-code-nerd-font${NC}"
