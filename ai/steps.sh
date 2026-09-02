#!/usr/bin/env bash
# description: AI component sync — dispatches to installed sub-tools
# AI parent dispatcher — sources all sub-tool steps.sh files and dispatches
# sync to each installed sub-tool.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

_AI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source all sub-tool steps.sh files so sync_<tool> functions are available.
for _ai_sub in "$_AI_DIR"/*/; do
  if [[ -f "${_ai_sub}steps.sh" ]]; then
    # shellcheck source=/dev/null
    . "${_ai_sub}steps.sh"
  fi
done
unset _ai_sub _AI_DIR

# The AI sub-tools whose steps have to run in a fixed relative order, first to
# last. Everything not named here is order-independent.
#
# claude before pi: step_pi_guidelines builds ~/.pi/agent/AGENTS.md out of
# ~/.claude/rules/, which step_claude_rules is what fills. Run the other way
# round and Pi's context file is one sync behind every rule file that was added
# since — an edit to an existing rule still propagates, because the installed
# rules are symlinks, which is exactly what makes the gap invisible until
# someone adds a file.
_AI_TOOL_ORDER=(claude pi)

# _ai_tool_named NEEDLE CANDIDATE... — true when NEEDLE is one of CANDIDATE.
_ai_tool_named() {
  local _needle="$1" _item
  shift
  for _item in "$@"; do
    if [[ "$_item" == "$_needle" ]]; then return 0; fi
  done
  return 1
}

# ai_tool_order TOOL... — prints TOOL... one per line, reordered so the sub-tools
# _AI_TOOL_ORDER constrains come first, in its order.
#
# The single owner of the constraint, because both entry points reach the same
# steps by different routes and neither's own order can be trusted to give it:
# sync_ai dispatches the operator's saved ai.tools list, and ai/setup.sh
# registers steps in the selection order restored from that same list. Whichever
# order the operator typed at the menu is what both used to get.
#
# Nothing is added and nothing is dropped: a tool the constraint does not name
# keeps its relative position, after the ones it does.
ai_tool_order() {
  local _known _tool
  for _known in "${_AI_TOOL_ORDER[@]}"; do
    if _ai_tool_named "$_known" "$@"; then printf '%s\n' "$_known"; fi
  done
  for _tool in "$@"; do
    if ! _ai_tool_named "$_tool" "${_AI_TOOL_ORDER[@]}"; then printf '%s\n' "$_tool"; fi
  done
  return 0
}

# sync_ai — dispatches to each installed AI sub-tool's sync function.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_ai() {
  local _tool
  local -a _tools=()
  while IFS= read -r _tool; do
    if [[ -n "$_tool" ]]; then _tools+=("$_tool"); fi
  done < <(state_get_list "ai.tools")

  if [[ ${#_tools[@]} -eq 0 ]]; then
    return 0
  fi

  while IFS= read -r _tool; do
    if declare -f "sync_${_tool}" > /dev/null; then
      "sync_${_tool}"
    fi
  done < <(ai_tool_order "${_tools[@]}")
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}AI sync${NC}\n"
  sync_ai
  echo
  success "AI sync complete!"
fi
