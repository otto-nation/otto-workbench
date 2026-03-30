#!/bin/bash
# Post-install summary for the ghostty sub-component.
# Sourced by ghostty/setup.sh — defines print_ghostty_summary().
# No top-level execution; safe to source without side effects.

# print_ghostty_summary — prints config location and useful next steps.
print_ghostty_summary() {
  echo
  echo -e "  ${CYAN}Ghostty${NC}"
  echo -e "  ${DIM}  Config: $GHOSTTY_CONFIG_FILE${NC}"
  echo -e "  ${DIM}  Browse themes: ghostty +list-themes${NC}"
  echo -e "  ${DIM}  Docs:          https://ghostty.org/docs/config${NC}"
}
