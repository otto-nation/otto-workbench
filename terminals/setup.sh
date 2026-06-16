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
WORKBENCH_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
. "$WORKBENCH_DIR/lib/ui.sh"

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

_STATE_KEY="terminals.tools"

SELECTED_TERMINALS=()
if state_load_selections "$_STATE_KEY" "$SCRIPT_DIR" SELECTED_TERMINALS; then
  _replaying=true
else
  select_terminals
  _replaying=false
fi

if [[ ${#SELECTED_TERMINALS[@]} -eq 0 ]]; then
  skip "Terminal setup"
  exit 0
fi

for _terminal in "${SELECTED_TERMINALS[@]}"; do
  echo
  # shellcheck source=/dev/null
  . "$SCRIPT_DIR/$_terminal/setup.sh"
  if [[ "$_replaying" != true ]]; then
    state_append_list "$_STATE_KEY" "$_terminal"
  fi
done
unset _terminal _replaying

echo
success "Terminal setup complete!"
