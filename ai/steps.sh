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

  # Refresh this machine's own rule layers before any harness installs from
  # them. No harness owns them, so this cannot live in one: a machine running
  # Pi alone used to get its rules only because Claude Code's sync had written
  # them, and so on a machine without it got none at all.
  "$AI_SRC_DIR/bin/workbench-rules" sync

  while IFS= read -r _tool; do
    [[ -z "$_tool" ]] && continue
    _tools+=("$_tool")
  done < <(state_get_list "ai.tools")

  # Guards the expansion below, which is an unbound-variable error on an empty
  # array in the bash the framework targets.
  if [[ ${#_tools[@]} -eq 0 ]]; then
    return 0
  fi

  for _tool in "${_tools[@]}"; do
    if declare -f "sync_${_tool}" > /dev/null; then
      "sync_${_tool}"
    fi
  done
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}AI sync${NC}\n"
  sync_ai
  echo
  success "AI sync complete!"
fi
