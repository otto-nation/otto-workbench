#!/bin/bash
# Post-install summary for the ai component.
# Sourced by install.sh after all components run — defines print_ai_summary().
# No top-level execution; safe to source without side effects.
#
# Note: detailed per-tool summaries (Claude, Kiro) are printed by ai/setup.sh during its
# own run. This summary provides the top-level recap and actionable next step.

# print_ai_summary — prints the remaining configuration step for AI task automation.
print_ai_summary() {
  echo
  echo -e "  ${CYAN}AI Tools${NC}"
  echo -e "  ${DIM}  Set your AI command for task automation:${NC}"
  echo -e "  ${DIM}  \$ \${EDITOR:-nano} ~/.config/task/taskfile.env${NC}"
}
