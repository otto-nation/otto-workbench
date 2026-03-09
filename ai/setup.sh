#!/bin/bash
# AI tools setup wizard.
#
# Usage: bash ai/setup.sh
#        (also called automatically by install.sh)
#
# What it does:
#   1. Discovers available AI tools from ai/*/steps.sh
#   2. Prompts you to select which tools to configure
#   3. Installs AI coding guidelines (CLAUDE.md / Kiro steering)
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

  echo -e "${BOLD}${BLUE}AI Tools Setup${NC}\n"
  echo "Which AI tools do you want to set up?"
  local i=1
  for tool in "${tools[@]}"; do
    echo "  [$i] $tool"
    i=$(( i + 1 ))
  done
  echo
  read -rp "Space-separated numbers (e.g. \"1 2\"): " selection
  echo

  local num
  for num in $selection; do
    if (( num >= 1 && num <= ${#tools[@]} )); then
      SELECTED_TOOLS+=("${tools[$((num - 1))]}")
    else
      warn "Unknown option: $num — ignored"
    fi
  done

  [[ ${#SELECTED_TOOLS[@]} -eq 0 ]] && { err "No tools selected. Exiting."; exit 1; }

  echo -ne "Setting up: "
  local t
  for t in "${SELECTED_TOOLS[@]}"; do echo -ne "${BOLD}${t}${NC}  "; done
  echo
}

# ─── Step runner ──────────────────────────────────────────────────────────────

STEPS=()

register_step() { STEPS+=("${1}|${2}"); }

run_steps() {
  local total=${#STEPS[@]} index=1 ran=0 skipped=0
  local step name fn

  for step in "${STEPS[@]}"; do
    name="${step%%|*}"
    fn="${step##*|}"
    echo -e "\n${DIM}[$index/$total]${NC} ${BOLD}$name${NC}"

    if confirm "  Run this step?"; then
      $fn
      ran=$(( ran + 1 ))
    else
      echo -e "  ${DIM}⊘ Skipped${NC}"
      skipped=$(( skipped + 1 ))
    fi

    index=$(( index + 1 ))
  done

  echo
  echo -e "${DIM}$ran run · $skipped skipped${NC}"
}

# ─── Shared step ──────────────────────────────────────────────────────────────

step_guidelines() {
  info "Installing AI coding guidelines"
  local general lang
  general=$(cat "$SCRIPT_DIR/guidelines/general.md")          || { err "Missing general.md"; return 1; }
  lang=$(cat    "$SCRIPT_DIR/guidelines/language-specific.md") || { err "Missing language-specific.md"; return 1; }
  tool_selected "claude" && _install_claude_guidelines "$general" "$lang"
  tool_selected "kiro"   && _install_kiro_guidelines   "$general" "$lang"
}

# ─── Main ─────────────────────────────────────────────────────────────────────

select_tools

register_step "Deploy AI coding guidelines" step_guidelines

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
