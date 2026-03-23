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
# shellcheck source=iterm/steps.sh
. "$SCRIPT_DIR/steps.sh"

if [[ "$OSTYPE" != "darwin"* ]]; then
  warn "iTerm2 setup is macOS-only — skipping"
  exit 0
fi

echo
info "iTerm2 color schemes:"
step_iterm_themes

echo
info "Font setup (manual step required — see post-install summary):"
echo -e "  ${DIM}  Settings → Profiles → Text → Font → FiraCodeNFM-Reg (size 13, ligatures on)${NC}"
