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

  # Show the active AI_COMMAND if configured, otherwise prompt the user to set one.
  # "Active" means an uncommented AI_COMMAND= line — matches load_ai_command() in lib/ai-commit.sh.
  local _active_cmd
  _active_cmd=$(grep -m1 '^AI_COMMAND=' "$TASK_CONFIG_DIR/taskfile.env" 2>/dev/null | sed 's/^AI_COMMAND=//')

  if [[ -n "$_active_cmd" ]]; then
    echo -e "  ${DIM}  AI command: ${_active_cmd}${NC}"
  else
    echo -e "  ${DIM}  Set your AI command for task automation:${NC}"
    echo -e "  ${DIM}  \$ \${EDITOR:-nano} $TASK_CONFIG_DIR/taskfile.env${NC}"
  fi
}
