#!/usr/bin/env bash
# Post-install summary for the ai component.
# Sourced by install.sh after all components run — defines print_ai_summary().
# No top-level execution; safe to source without side effects.

# print_ai_summary — prints AI-specific summary info.
# AI_COMMAND and GH_TOKEN status are now shown by the central summary in lib/summary.sh.
print_ai_summary() {
  echo
  echo -e "  ${CYAN}AI Tasks${NC}"
  echo -e "  ${DIM}  Available commands: task commit, task pr:create, task review${NC}"
}
