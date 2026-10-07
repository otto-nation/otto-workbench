#!/usr/bin/env bash
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
WORKBENCH_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
. "$WORKBENCH_DIR/lib/ui.sh"

# The AI dispatcher sources every tool's steps.sh — any subdirectory containing
# one is a tool. Sourced rather than re-globbed here so setup and sync read the
# same list of tools.
# shellcheck source=./steps.sh
. "$SCRIPT_DIR/steps.sh"

# ─── Helpers ──────────────────────────────────────────────────────────────────

# prompt_secret LABEL VAR — hidden read into a named variable.
prompt_secret() {
  local label=$1
  local -n __out=$2
  local value
  read -rsp "${label}: " value
  echo
  __out="$value"
}

# ─── Tool selection ───────────────────────────────────────────────────────────

SELECTED_TOOLS=()

# _ai_discover_tools — prints the name of each AI tool subdirectory that
# contains a steps.sh, one per line. Caller reads into an array. Delegates to
# ai/steps.sh's ai_sub_tool_dirs so this list and the one that sourced
# sync_<tool> in can't independently disagree about what counts as a tool.
_ai_discover_tools() {
  local dir
  while IFS= read -r dir; do
    basename "$dir"
  done < <(ai_sub_tool_dirs "$SCRIPT_DIR")
}

# select_tools — presents available AI tools and populates SELECTED_TOOLS.
# Uses _AVAILABLE_TOOLS if already populated, otherwise discovers from disk.
select_tools() {
  local tools=("${_AVAILABLE_TOOLS[@]}")
  if [[ ${#tools[@]} -eq 0 ]]; then
    while IFS= read -r tool; do tools+=("$tool"); done < <(_ai_discover_tools)
  fi

  if [[ ${#tools[@]} -eq 0 ]]; then
    err "No AI tools found in $SCRIPT_DIR"
    exit 1
  fi

  echo -e "${BOLD}${BLUE}AI Tools Setup${NC}"
  echo
  info "Which AI tools do you want to set up?"
  local i=1
  local tool
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

  local tools_display="" t
  for t in "${SELECTED_TOOLS[@]}"; do tools_display+="${BOLD}${t}${NC}  "; done
  info "Setting up:  ${tools_display}"
}

# ─── Step runner ──────────────────────────────────────────────────────────────
# register_step and run_steps are defined in lib/ui.sh

# shellcheck disable=SC2034  # consumed by register_step/run_steps in lib/ui.sh
STEPS=()

# ─── Main ─────────────────────────────────────────────────────────────────────

_STATE_KEY="ai.tools"

_AVAILABLE_TOOLS=()
while IFS= read -r _t; do _AVAILABLE_TOOLS+=("$_t"); done < <(_ai_discover_tools)

if ! state_load_selections "$_STATE_KEY" "$SCRIPT_DIR" SELECTED_TOOLS _AVAILABLE_TOOLS; then
  select_tools
fi

# The harness-neutral CLIs first, unconditionally: pr, otto-log, ceiling-scan
# and the rest serve whichever harness the operator picked, so gating them on a
# selection would leave a Pi-only machine with none of them on PATH.
sync_component_bin "$SCRIPT_DIR"

# This machine's own rule layers, likewise ahead of any harness: every harness
# step installs from them, and none of them owns them.
"$SCRIPT_DIR/bin/workbench-rules" sync

# Generated git and tool-context files sit in the same window: a Pi-only
# install has no Claude Code step to hang them off, and every harness reads them.
# Intentionally unconditional and outside register_step/run_steps, like the
# workbench-rules sync call above it: this is a user-facing behavior change
# from the old per-step-skippable "Tool context" Claude step, not an oversight.
ai_generate_rules
# Before any harness reads model config out of ~/.env.local.
ai_adopt_legacy_model_vars

# Then each selected tool's own scripts, before running steps — a tool's setup
# steps may call its own bin scripts.
for _tool in "${SELECTED_TOOLS[@]}"; do
  _tool_dir="$SCRIPT_DIR/$_tool"
  if [[ -d "$_tool_dir/bin" ]]; then
    sync_component_bin "$_tool_dir"
  fi
done
unset _tool_dir

# Framework contract: missing register_<tool>_steps is a hard error — the tool's
# steps.sh is broken and cannot run. Individual step failures are soft (warn + continue).
#
# No ordering is imposed on the tools: each one's steps read the shared rule
# layers refreshed above rather than another tool's installed output, so the
# order the operator typed at the menu is order enough.
for _tool in "${SELECTED_TOOLS[@]}"; do
  declare -f "register_${_tool}_steps" > /dev/null \
    || { err "register_${_tool}_steps is not defined — check ${_tool}/steps.sh"; exit 1; }
  "register_${_tool}_steps"
done

run_steps

# Record installed component and sub-tools in state. The selection replaces the
# saved one wholesale rather than being appended to it, so deselecting a tool
# takes effect; until this line the previous selection is still what sync reads.
state_record "ai"
state_set_list "$_STATE_KEY" "${SELECTED_TOOLS[@]}"

# ─── GitHub token file ────────────────────────────────────────────────────

# configure_gh_token — ensures $TASKFILE_ENV holds the GH_TOKEN template and
# offers to edit it when no token is set yet. "Set" means an uncommented
# GH_TOKEN or GH_TOKEN__<ORG> line with a value — the tiers gh_token.py reads.
configure_gh_token() {
  echo; info "GitHub token for pr create / pr describe --post"
  ai_scaffold_gh_token_env "$TASKFILE_ENV"
  if grep -qE "$GH_TOKEN_SET_RE" "$TASKFILE_ENV"; then
    success "GH_TOKEN already configured"
    return 0
  fi
  [[ -t 0 ]] || { warn "Edit $TASKFILE_ENV before running pr create"; return 0; }
  if confirm "  Add a GitHub token now?"; then
    ${EDITOR:-nano} "$TASKFILE_ENV"
    success "Token file updated"
  else
    warn "Edit $TASKFILE_ENV before running pr create"
  fi
}

configure_gh_token

echo
success "AI tools setup complete!"
for _tool in "${SELECTED_TOOLS[@]}"; do
  if declare -f "print_${_tool}_summary" > /dev/null; then "print_${_tool}_summary"; fi
done
unset _tool
