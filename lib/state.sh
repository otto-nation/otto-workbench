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

# state_list — prints all entries in the state file, one per line.
state_list() {
  [[ -f "$INSTALLED_STATE_FILE" ]] || return 0
  cat "$INSTALLED_STATE_FILE"
}

# state_prune_orphans — removes state entries that have no matching steps.sh.
# Compares entries against discovered step files. Entries with no corresponding
# component directory are pruned and reported via warn().
# Requires lib/components.sh to be sourced (provides discover_step_files).
state_prune_orphans() {
  state_file_exists || return 0

  # Build set of valid component paths from step files
  local -a _step_files=()
  discover_step_files _step_files

  local -A _valid_paths=()
  local _f _path
  for _f in "${_step_files[@]}"; do
    _path="${_f#"$WORKBENCH_DIR/"}"
    _path="${_path%/steps.sh}"
    _valid_paths["$_path"]=1
  done

  # Check each state entry against valid paths
  local _entry
  local -a _orphans=()
  while IFS= read -r _entry; do
    [[ -z "$_entry" ]] && continue
    if [[ -z "${_valid_paths[$_entry]:-}" ]]; then
      _orphans+=("$_entry")
    fi
  done < "$INSTALLED_STATE_FILE"

  # Remove orphaned entries
  for _entry in "${_orphans[@]}"; do
    state_remove "$_entry"
    warn "Pruned orphaned state entry: $_entry (no matching component)"
  done
}

# state_detect_installed — detects currently installed components and records them.
# Uses heuristics (config files, symlinks, directories) to determine what is present.
# Called by the initial-state migration and by `otto-workbench state regenerate`.
state_detect_installed() {
  # Core components — always present in a workbench install
  state_record "bin"
  state_record "git"
  state_record "zsh"

  # Docker — detect by state symlink presence
  if [[ -L "$DOCKER_RUNTIME_ALIASES" ]]; then
    state_record "docker"
  fi

  # AI / Claude — detect by settings file
  if [[ -f "$CLAUDE_SETTINGS_FILE" ]]; then
    state_record "ai"
    state_record "ai/claude"
  fi

  # AI / Serena — detect by serena-mcp symlink in ~/.local/bin
  if [[ -L "$LOCAL_BIN_DIR/serena-mcp" ]]; then
    state_record "ai"
    state_record "ai/serena"
  fi

  # Terminals / Ghostty — detect by config directory
  if [[ -d "$GHOSTTY_CONFIG_DIR" ]]; then
    state_record "terminals"
    state_record "terminals/ghostty"
  fi

  # Editors / Zed — detect by settings file
  if [[ -f "$ZED_SETTINGS_FILE" ]]; then
    state_record "editors"
    state_record "editors/zed"
  fi

  # Editors / Sublime — detect by settings file
  if [[ -f "$SUBLIME_SETTINGS_FILE" ]]; then
    state_record "editors"
    state_record "editors/sublime"
  fi
}
