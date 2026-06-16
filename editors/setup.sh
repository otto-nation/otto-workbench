#!/usr/bin/env bash
# Editor setup.
#
# Usage: ./editors/setup.sh
#        (also called automatically by otto-workbench install)
#
# What it does:
#   1. Prompts for which editor(s) to configure (multi-select)
#   2. Runs setup for each selected editor
#
# Re-running is safe — managed settings are merged; local customizations are preserved.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKBENCH_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
. "$WORKBENCH_DIR/lib/ui.sh"

if [[ "$OSTYPE" != "darwin"* ]]; then
  warn "Editor setup is macOS-only — skipping"
  exit 0
fi

select_editors() {
  local _sel
  select_subdirs _sel "$SCRIPT_DIR" "Which editor(s) would you like to configure?" --default all \
    || exit 1

  SELECTED_EDITORS=()
  # shellcheck disable=SC2086  # word-splitting intentional — _sel is space-separated names
  for _e in $_sel; do SELECTED_EDITORS+=("$_e"); done
}

echo -e "${BOLD}${BLUE}Editor setup${NC}\n"

_STATE_KEY="editors.tools"

SELECTED_EDITORS=()
if state_load_selections "$_STATE_KEY" "$SCRIPT_DIR" SELECTED_EDITORS; then
  _replaying=true
else
  select_editors
  _replaying=false
fi

if [[ ${#SELECTED_EDITORS[@]} -eq 0 ]]; then
  skip "Editor setup"
  exit 0
fi

_EDITORS_DIR="$SCRIPT_DIR"
for _editor in "${SELECTED_EDITORS[@]}"; do
  echo
  # shellcheck source=/dev/null
  . "$_EDITORS_DIR/$_editor/setup.sh"
  if [[ "$_replaying" != true ]]; then
    state_append_list "$_STATE_KEY" "$_editor"
  fi
done
unset _editor _replaying

echo
success "Editor setup complete!"
