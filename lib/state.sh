#!/usr/bin/env bash
# Component installation state tracking.
#
# Tracks which components and sub-tools have been installed in a simple
# line-delimited state file. One entry per line: "bin", "git", "ai/claude", etc.
#
# Usage (from scripts that already source lib/ui.sh):
#   state_record "ai"           # record a component
#   state_record "ai/claude"    # record a sub-tool
#   state_is_installed "ai"     # returns 0 if installed
#   state_remove "ai/claude"    # remove an entry
#   state_file_exists           # returns 0 if state file exists

# Guard: constants must be loaded (provides INSTALLED_STATE_FILE)
if [[ -z "${INSTALLED_STATE_FILE:-}" ]]; then
  echo "ERROR: lib/state.sh requires INSTALLED_STATE_FILE (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

# state_record ENTRY — appends entry to the state file. Idempotent.
state_record() {
  local entry="$1"
  mkdir -p "$(dirname "$INSTALLED_STATE_FILE")"
  if ! grep -qxF "$entry" "$INSTALLED_STATE_FILE" 2>/dev/null; then
    echo "$entry" >> "$INSTALLED_STATE_FILE"
  fi
}

# state_is_installed ENTRY — returns 0 if entry is in the state file.
state_is_installed() {
  grep -qxF "$1" "$INSTALLED_STATE_FILE" 2>/dev/null
}

# state_remove ENTRY — removes an entry from the state file.
state_remove() {
  local entry="$1"
  [[ -f "$INSTALLED_STATE_FILE" ]] || return 0
  local tmp
  tmp=$(mktemp)
  grep -vxF "$entry" "$INSTALLED_STATE_FILE" > "$tmp" 2>/dev/null || true
  mv "$tmp" "$INSTALLED_STATE_FILE"
}

# state_file_exists — returns 0 if the state file exists.
state_file_exists() {
  [[ -f "$INSTALLED_STATE_FILE" ]]
}
