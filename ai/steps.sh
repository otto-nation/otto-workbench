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

# ai_sub_tool_dirs DIR — prints the tool subdirectories of DIR that contain a
# steps.sh, one per line. The single definition of "what counts as an AI
# sub-tool": used here to source every steps.sh, and by ai/setup.sh's
# _ai_discover_tools to build the tool list it presents, so the two glob the
# same rule instead of two copies that could drift apart.
ai_sub_tool_dirs() {
  local base="$1" dir
  for dir in "$base"/*/; do
    if [[ -f "${dir}steps.sh" ]]; then printf '%s\n' "$dir"; fi
  done
}

# Source all sub-tool steps.sh files so sync_<tool> functions are available.
while IFS= read -r _ai_sub; do
  # shellcheck source=/dev/null
  . "${_ai_sub}steps.sh"
done < <(ai_sub_tool_dirs "$_AI_DIR")
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

  # ai/bin holds the CLIs that serve every harness — pr, otto-log, ceiling-scan
  # and the rest. They install ahead of the selection check and regardless of
  # what it holds: a machine running Pi alone still needs them on PATH, and a
  # machine with no sub-tool selected at all still has them in its registry.
  sync_component_bin "$AI_SRC_DIR"

  while IFS= read -r _tool; do
    [[ -z "$_tool" ]] && continue
    _tools+=("$_tool")
  done < <(state_get_list "ai.tools")

  # Redundant with the loop below (ai_tool_order on an empty list prints
  # nothing), kept as an explicit short-circuit so "no tools configured" reads
  # as its own case rather than an empty loop.
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
