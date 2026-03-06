#!/bin/bash
# AI tools setup wizard.
#
# Usage: bash ai/setup.sh
#        (also called automatically by install.sh)
#
# What it does:
#   1. Prompts you to select which AI tools to configure (Claude Code, Kiro)
#   2. Installs AI coding guidelines (CLAUDE.md / Kiro steering)
#   3. Syncs Claude Code settings (~/.claude/settings.json) from repo template
#   4. Registers MCP servers (Serena, Sequential Thinking, Context7)
#   5. Installs Claude Code skills and agents from ai/claude/
#   6. Installs Kiro agent configs from ai/kiro/
#
# Each step is individually confirmable — skip anything you don't need.
# Re-running is safe: symlinks are updated silently; real files prompt before overwrite.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/ui.sh"
. "$SCRIPT_DIR/claude/steps.sh"
. "$SCRIPT_DIR/kiro/steps.sh"

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
  echo -e "${BOLD}${BLUE}AI Tools Setup${NC}\n"
  echo "Which AI tools do you want to set up?"
  echo "  [1] Claude Code"
  echo "  [2] Kiro"
  echo
  read -rp "Space-separated numbers (e.g. \"1 2\"): " selection
  echo

  local num
  for num in $selection; do
    case $num in
      1) SELECTED_TOOLS+=("claude") ;;
      2) SELECTED_TOOLS+=("kiro")   ;;
      *) warn "Unknown option: $num — ignored" ;;
    esac
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

if tool_selected "claude"; then
  if ! command -v claude >/dev/null 2>&1; then
    warn "Claude Code (claude) not found in PATH — skipping Claude setup steps"
  else
    register_step "Claude Code settings"     step_claude_settings
    register_step "MCP: Serena"              step_mcp_serena
    register_step "MCP: Sequential Thinking" step_mcp_sequential_thinking
    register_step "MCP: Context7"            step_mcp_context7
    register_step "Claude Code skills"       step_claude_skills
    register_step "Claude Code agents"       step_claude_agents
  fi
fi

tool_selected "kiro" && register_step "Kiro agent configs" step_kiro_agents

run_steps

echo
echo -e "${BOLD}${GREEN}✓ AI tools setup complete!${NC}"
tool_selected "claude" && print_claude_summary
