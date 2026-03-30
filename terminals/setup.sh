#!/bin/bash
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
  local terminals=() dir

  # Discover terminals dynamically — any subdirectory containing setup.sh qualifies
  for dir in "$SCRIPT_DIR"/*/; do
    [[ -f "${dir}setup.sh" ]] && terminals+=("$(basename "$dir")")
  done

  if [[ ${#terminals[@]} -eq 0 ]]; then
    err "No terminal setups found in $SCRIPT_DIR"
    exit 1
  fi

  info "Which terminal(s) would you like to configure?"
  local i=1
  for terminal in "${terminals[@]}"; do
    echo "  [$i] $terminal"
    i=$(( i + 1 ))
  done
  echo

  local _sel
  select_menu _sel "${#terminals[@]}" --default skip
  [[ -z "$_sel" ]] && { SELECTED_TERMINALS=(); return; }

  SELECTED_TERMINALS=()
  local num
  for num in $_sel; do
    SELECTED_TERMINALS+=("${terminals[$(( num - 1 ))]}")
  done
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
