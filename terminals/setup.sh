#!/usr/bin/env bash
# Terminal emulator setup.
#
# Usage: bash terminals/setup.sh
#        (also called automatically by install.sh)
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
select_terminals

if [[ ${#SELECTED_TERMINALS[@]} -eq 0 ]]; then
  skip "Terminal setup"
  exit 0
fi

for _terminal in "${SELECTED_TERMINALS[@]}"; do
  echo
  # shellcheck source=/dev/null
  . "$SCRIPT_DIR/$_terminal/setup.sh"
done
unset _terminal

echo
success "Terminal setup complete!"
