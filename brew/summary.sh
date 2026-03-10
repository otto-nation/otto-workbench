#!/bin/bash
# Post-install summary for the brew component.
# Sourced by install.sh after all components run — defines print_brew_summary().
# No top-level execution; safe to source without side effects.

# print_brew_summary — prints next steps for day-to-day Homebrew usage.
print_brew_summary() {
  echo
  echo -e "  ${CYAN}Homebrew${NC}"
  echo -e "  ${DIM}  After installing new packages, sync the Brewfile:${NC}"
  echo -e "  ${DIM}  \$ task --global brew:dump${NC}"
  echo -e "  ${DIM}  Work stacks: brew bundle --file=brew/work/<stack>.Brewfile${NC}"
}
