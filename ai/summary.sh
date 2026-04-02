#!/bin/bash
# Post-install summary for the ai component.
# Sourced by install.sh after all components run — defines print_ai_summary().
# No top-level execution; safe to source without side effects.

# print_ai_summary — delegates to per-tool summary functions defined in steps.sh files,
# then prints the remaining configuration step for AI task automation.
print_ai_summary() {
  # Per-tool summaries (Claude, Kiro) are printed by ai/setup.sh immediately
  # after setup — this function only prints the shared AI Tasks section.

  echo
  echo -e "  ${CYAN}AI Tasks${NC}"

  # Show the active AI_COMMAND if configured, otherwise prompt the user to set one.
  # "Active" means an uncommented AI_COMMAND= line — matches load_ai_command() in lib/ai/core.sh.
  local _active_cmd
  _active_cmd=$(grep -m1 '^AI_COMMAND=' "$TASK_CONFIG_DIR/taskfile.env" 2>/dev/null | sed 's/^AI_COMMAND=//')

  if [[ -n "$_active_cmd" ]]; then
    echo -e "  ${DIM}  AI command: ${_active_cmd}${NC}"
  else
    echo -e "  ${DIM}  Set your AI command for task automation:${NC}"
    echo -e "  ${DIM}  \$ \${EDITOR:-nano} $TASK_CONFIG_DIR/taskfile.env${NC}"
  fi
}
