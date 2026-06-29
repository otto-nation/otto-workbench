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
unset _ai_sub

# sync_ai — dispatches to each installed AI sub-tool's sync function.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_ai() {
  local _tool
  while IFS= read -r _tool; do
    [[ -z "$_tool" ]] && continue
    if declare -f "sync_${_tool}" > /dev/null; then
      "sync_${_tool}"
    fi
  done < <(state_get_list "ai.tools")
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}AI sync${NC}\n"
  sync_ai
  echo
  success "AI sync complete!"
fi
