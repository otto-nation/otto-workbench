#!/usr/bin/env bash
# Post-install summary for the iterm component.
# Sourced by install.sh after all components run — defines print_iterm_summary().
# No top-level execution; safe to source without side effects.

# print_iterm_summary — prints the manual configuration steps required after iTerm2 setup.
print_iterm_summary() {
  echo
  echo -e "  ${CYAN}iTerm2 — manual steps required${NC}"
  echo -e "  ${DIM}  Color preset: Settings → Profiles → Colors → Color Presets${NC}"
  echo -e "  ${DIM}  Font:         Settings → Profiles → Text → Font → FiraCodeNFM-Reg (size 13)${NC}"
  echo -e "  ${DIM}                Enable: Use ligatures${NC}"
  echo -e "  ${DIM}  (Install font first if needed: brew install --cask font-fira-code-nerd-font)${NC}"
}
