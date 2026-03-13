#!/bin/bash
# AI tools setup wizard.
#
# Usage: bash ai/setup.sh
#        (also called automatically by install.sh)
#
# What it does:
#   1. Discovers available AI tools from ai/*/steps.sh
#   2. Prompts you to select which tools to configure
#   3. Runs each selected tool's registered setup steps (rules symlinked per tool)
#   4. Runs each selected tool's registered setup steps
#
# Adding a new tool: create ai/<toolname>/steps.sh with a register_<toolname>_steps function.
# Each step is individually confirmable — skip anything you don't need.
# Re-running is safe: symlinks are updated silently; real files prompt before overwrite.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/ui.sh"

# Source all tool step files — any subdirectory containing steps.sh is a tool
for _dir in "$SCRIPT_DIR"/*/; do
  # shellcheck source=/dev/null
  [[ -f "${_dir}steps.sh" ]] && . "${_dir}steps.sh"
done
unset _dir

# ─── Helpers ──────────────────────────────────────────────────────────────────

# prompt_secret LABEL VAR — hidden read into a named variable.
prompt_secret() {
  local label=$1 var=$2 value
  read -rsp "${label}: " value
  echo
  printf -v "$var" '%s' "$value"
}

# ─── Tool selection ───────────────────────────────────────────────────────────

SELECTED_TOOLS=()

tool_selected() {
  local t
  for t in "${SELECTED_TOOLS[@]}"; do [[ "$t" == "$1" ]] && return 0; done
  return 1
}

select_tools() {
  local tools=()
  local dir
  for dir in "$SCRIPT_DIR"/*/; do
    [[ -f "${dir}steps.sh" ]] && tools+=("$(basename "$dir")")
  done

  if [[ ${#tools[@]} -eq 0 ]]; then
    err "No AI tools found in $SCRIPT_DIR"
    exit 1
  fi

  echo -e "${BOLD}${BLUE}AI Tools Setup${NC}"
  echo
  info "Which AI tools do you want to set up?"
  local i=1
  for tool in "${tools[@]}"; do
    echo -e "  ${CYAN}[$i]${NC} $tool"
    i=$(( i + 1 ))
  done
  echo

  local _sel
  select_menu _sel "${#tools[@]}" --default all
  [[ -z "$_sel" ]] && { info "No tools selected — exiting."; exit 0; }

  local num
  for num in $_sel; do
    SELECTED_TOOLS+=("${tools[$((num - 1))]}")
  done

  local tools_display=""
  local t
  for t in "${SELECTED_TOOLS[@]}"; do tools_display+="${BOLD}${t}${NC}  "; done
  info "Setting up:  ${tools_display}"
}

# ─── Step runner ──────────────────────────────────────────────────────────────
# register_step and run_steps are defined in lib/ui.sh

# shellcheck disable=SC2034  # consumed by register_step/run_steps in lib/ui.sh
STEPS=()

# ─── Main ─────────────────────────────────────────────────────────────────────

select_tools

for _tool in "${SELECTED_TOOLS[@]}"; do
  "register_${_tool}_steps"
done

run_steps

echo
echo -e "${BOLD}${GREEN}✓ AI tools setup complete!${NC}"
for _tool in "${SELECTED_TOOLS[@]}"; do
  declare -f "print_${_tool}_summary" > /dev/null && "print_${_tool}_summary"
done
unset _tool

# ─── AI command configuration ─────────────────────────────────────────────────

# configure_ai_command — runs `task --global ai:setup` to create ~/.config/task/taskfile.env,
# then optionally opens that file in $EDITOR so the user can set their AI_COMMAND preference.
configure_ai_command() {
  command -v task >/dev/null 2>&1 || return

  echo; info "Taskfile AI command"
  task --global ai:setup

  echo
  if confirm "  Configure your AI command now?"; then
    ${EDITOR:-nano} ~/.config/task/taskfile.env
    success "AI configuration updated"
  else
    warn "Remember to edit $HOME/.config/task/taskfile.env before using AI tasks"
  fi
}

configure_ai_command
