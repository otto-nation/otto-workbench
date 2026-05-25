#!/usr/bin/env bash
# Terminal emulator setup.
#
# Usage: ./terminals/setup.sh
#        (also called automatically by otto-workbench install)
#
# What it does:
#   1. Prompts for which terminal(s) to configure (multi-select)
#   2. Runs setup for each selected terminal
#
# Re-running is safe — existing config is never overwritten.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/ui.sh"

if [[ "$OSTYPE" != "darwin"* ]]; then
  warn "Terminal setup is macOS-only — skipping"
  exit 0
fi

# ─── Terminal selection ───────────────────────────────────────────────────────

select_terminals() {
  local _sel
  select_subdirs _sel "$SCRIPT_DIR" "Which terminal(s) would you like to configure?" --default all \
    || exit 1

  SELECTED_TERMINALS=()
  # shellcheck disable=SC2086  # word-splitting intentional — _sel is space-separated names
  for _t in $_sel; do SELECTED_TERMINALS+=("$_t"); done
}

# ─── Main ─────────────────────────────────────────────────────────────────────

echo -e "${BOLD}${BLUE}Terminal setup${NC}\n"

SELECTED_TERMINALS=()
_replaying=false
_saved_tools=$(state_get_list "terminals.tools")
if [[ -n "$_saved_tools" ]] && [[ "${WORKBENCH_INTERACTIVE:-}" != "1" ]]; then
  while IFS= read -r _t; do
    if [[ -d "$SCRIPT_DIR/$_t" ]]; then SELECTED_TERMINALS+=("$_t"); fi
  done <<< "$_saved_tools"
  if [[ ${#SELECTED_TERMINALS[@]} -gt 0 ]]; then
    _replaying=true
    info "Using saved selections: ${SELECTED_TERMINALS[*]}"
  else
    select_terminals
  fi
else
  state_set "terminals.tools" "[]"
  select_terminals
fi
unset _saved_tools _t

if [[ ${#SELECTED_TERMINALS[@]} -eq 0 ]]; then
  skip "Terminal setup"
  exit 0
fi

for _terminal in "${SELECTED_TERMINALS[@]}"; do
  echo
  # shellcheck source=/dev/null
  . "$SCRIPT_DIR/$_terminal/setup.sh"
  if [[ "$_replaying" == false ]]; then
    state_append_list "terminals.tools" "$_terminal"
  fi
done
unset _terminal _replaying

echo
success "Terminal setup complete!"
