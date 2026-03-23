#!/bin/bash
# AI tools setup wizard.
#
# Usage: bash ai/setup.sh
#        (also called automatically by install.sh)
#
# What it does:
#   1. Discovers available AI tools from ai/*/steps.sh
#   2. Prompts you to select which tools to configure
#   3. Runs each selected tool's registered setup steps
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
success "AI tools setup complete!"
for _tool in "${SELECTED_TOOLS[@]}"; do
  declare -f "print_${_tool}_summary" > /dev/null && "print_${_tool}_summary"
done
unset _tool

# ─── AI command configuration ─────────────────────────────────────────────────

# configure_ai_command — ensures ~/.config/task/taskfile.env exists and contains
# an active AI_COMMAND. Skips all prompts if a command is already configured.
#
# "Active" means an uncommented AI_COMMAND= line — same definition used by
# load_ai_command() in lib/ai-commit.sh at runtime.
configure_ai_command() {
  command -v task >/dev/null 2>&1 || return

  local env_file="$TASKFILE_ENV"
  local active_cmd
  active_cmd=$(grep -m1 '^AI_COMMAND=' "$env_file" 2>/dev/null | sed 's/^AI_COMMAND=//')

  echo; info "Taskfile AI command"

  if [[ -n "$active_cmd" ]]; then
    success "AI command already configured: ${active_cmd}"
    return
  fi

  # File absent or all examples commented out — create it and offer to configure
  task --global ai:setup

  echo
  if confirm "  Configure your AI command now?"; then
    ${EDITOR:-nano} "$env_file"
    success "AI configuration updated"
  else
    warn "Remember to edit $env_file before using AI tasks"
  fi
}

configure_ai_command
