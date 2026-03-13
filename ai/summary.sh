#!/bin/bash
# Post-install summary for the ai component.
# Sourced by install.sh after all components run — defines print_ai_summary().
# No top-level execution; safe to source without side effects.

# print_ai_summary — delegates to per-tool summary functions defined in steps.sh files,
# then prints the remaining configuration step for AI task automation.
print_ai_summary() {
  local _ai_dir
  _ai_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  # shellcheck source=/dev/null
  [[ -f "$_ai_dir/claude/steps.sh" ]] && . "$_ai_dir/claude/steps.sh"
  # shellcheck source=/dev/null
  [[ -f "$_ai_dir/kiro/steps.sh" ]] && . "$_ai_dir/kiro/steps.sh"

  if declare -f print_claude_summary > /dev/null 2>&1; then
    print_claude_summary
  fi
  if declare -f print_kiro_summary > /dev/null 2>&1; then
    print_kiro_summary
  fi

  echo
  echo -e "  ${CYAN}AI Tasks${NC}"
  echo -e "  ${DIM}  Set your AI command for task automation:${NC}"
  echo -e "  ${DIM}  \$ \${EDITOR:-nano} $TASK_CONFIG_DIR/taskfile.env${NC}"
}
