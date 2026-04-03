#!/bin/bash
# Editor setup.
#
# Usage: bash editors/setup.sh
#        (also called automatically by install.sh)
#
# What it does:
#   1. Prompts for which editor(s) to configure (multi-select)
#   2. Runs setup for each selected editor
#
# Re-running is safe — managed settings are merged; local customizations are preserved.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/ui.sh"

if [[ "$OSTYPE" != "darwin"* ]]; then
  warn "Editor setup is macOS-only — skipping"
  exit 0
fi

select_editors() {
  local editors=() dir

  for dir in "$SCRIPT_DIR"/*/; do
    [[ -f "${dir}setup.sh" ]] && editors+=("$(basename "$dir")")
  done

  if [[ ${#editors[@]} -eq 0 ]]; then
    err "No editor setups found in $SCRIPT_DIR"
    exit 1
  fi

  info "Which editor(s) would you like to configure?"
  local i=1 editor
  for editor in "${editors[@]}"; do
    echo "  [$i] $editor"
    i=$(( i + 1 ))
  done
  echo

  local _sel
  select_menu _sel "${#editors[@]}" --default all
  [[ -z "$_sel" ]] && { SELECTED_EDITORS=(); return; }

  SELECTED_EDITORS=()
  local num
  for num in $_sel; do
    SELECTED_EDITORS+=("${editors[$(( num - 1 ))]}")
  done
}

echo -e "${BOLD}${BLUE}Editor setup${NC}\n"

SELECTED_EDITORS=()
select_editors

if [[ ${#SELECTED_EDITORS[@]} -eq 0 ]]; then
  skip "Editor setup"
  exit 0
fi

_EDITORS_DIR="$SCRIPT_DIR"
for _editor in "${SELECTED_EDITORS[@]}"; do
  echo
  # shellcheck source=/dev/null
  . "$_EDITORS_DIR/$_editor/setup.sh"
done
unset _editor

echo
success "Editor setup complete!"
