#!/usr/bin/env bash
# Component installation state tracking via YAML.
#
# Tracks which components and sub-tools are installed in a structured
# YAML file (~/.config/workbench/install.yml). Core components (bin, git,
# zsh, task) are omitted — they always sync.
#
# Usage (from scripts that already source lib/ui.sh):
#   state_record "ai"           # record a component
#   state_record "ai/claude"    # record a sub-tool
#   state_is_installed "ai"     # returns 0 if installed
#   state_remove "ai/claude"    # remove an entry
#   state_file_exists           # returns 0 if state file exists

# Guard: constants must be loaded (provides INSTALL_YML_FILE)
if [[ -z "${INSTALL_YML_FILE:-}" ]]; then
  echo "ERROR: lib/state.sh requires INSTALL_YML_FILE (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi


# _state_ensure_yml — creates the YAML file with an empty components map if missing.
_state_ensure_yml() {
  mkdir -p "$(dirname "$INSTALL_YML_FILE")"
  [[ -f "$INSTALL_YML_FILE" ]] || echo "components: {}" > "$INSTALL_YML_FILE"
}

# _state_is_core ENTRY — returns 0 if entry is a core component (always synced).
_state_is_core() {
  [[ " $CORE_COMPONENTS " == *" $1 "* ]]
}

# state_record ENTRY — records a component or sub-tool in install.yml. Idempotent.
state_record() {
  local entry="$1"
  _state_is_core "$entry" && return 0
  _state_ensure_yml

  case "$entry" in
    mise)
      yq -i '.components.mise = true' "$INSTALL_YML_FILE"
      ;;
    */*)
      local parent="${entry%%/*}" child="${entry#*/}"
      v="$child" yq -i '.components.'"$parent"' |= (. // {}) | .components.'"$parent"'.tools |= ((. // []) + [strenv(v)] | unique)' "$INSTALL_YML_FILE"
      ;;
    *)
      yq -i '.components.'"$entry"' |= (. // {})' "$INSTALL_YML_FILE"
      ;;
  esac
}

# state_is_installed ENTRY — returns 0 if entry is recorded in install.yml.
state_is_installed() {
  local entry="$1"
  _state_is_core "$entry" && return 0
  [[ -f "$INSTALL_YML_FILE" ]] || return 1

  case "$entry" in
    */*)
      local parent="${entry%%/*}" child="${entry#*/}"
      v="$child" yq -e '.components.'"$parent"'.tools[] | select(. == strenv(v))' "$INSTALL_YML_FILE" &>/dev/null
      ;;
    *)
      yq -e '.components.'"$entry" "$INSTALL_YML_FILE" &>/dev/null
      ;;
  esac
}

# state_remove ENTRY — removes a component or sub-tool from install.yml.
state_remove() {
  local entry="$1"
  _state_is_core "$entry" && return 0
  [[ -f "$INSTALL_YML_FILE" ]] || return 0

  case "$entry" in
    */*)
      local parent="${entry%%/*}" child="${entry#*/}"
      v="$child" yq -i 'del(.components.'"$parent"'.tools[] | select(. == strenv(v)))' "$INSTALL_YML_FILE"
      ;;
    *)
      yq -i 'del(.components.'"$entry"')' "$INSTALL_YML_FILE"
      ;;
  esac
}

# state_file_exists — returns 0 if install.yml (or legacy state file) exists.
state_file_exists() {
  [[ -f "$INSTALL_YML_FILE" ]] || [[ -f "$INSTALLED_STATE_FILE" ]]
}

# state_list — prints all installed entries, one per line (flat format for compat).
state_list() {
  [[ -f "$INSTALL_YML_FILE" ]] || return 0
  local comp tool
  while IFS= read -r comp; do
    [[ -z "$comp" ]] && continue
    echo "$comp"
    while IFS= read -r tool; do
      [[ -z "$tool" ]] && continue
      echo "$comp/$tool"
    done < <(yq '.components.'"$comp"'.tools | (. // []) | .[]' "$INSTALL_YML_FILE" 2>/dev/null)
  done < <(yq '.components | keys | .[]' "$INSTALL_YML_FILE" 2>/dev/null)
}

# state_prune_orphans — removes YAML entries that have no matching steps.sh.
# Requires lib/components.sh to be sourced (provides discover_step_files).
state_prune_orphans() {
  [[ -f "$INSTALL_YML_FILE" ]] || return 0

  local -a _step_files=()
  discover_step_files _step_files

  local -A _valid_paths=()
  local _f _path
  for _f in "${_step_files[@]}"; do
    _path="${_f#"$WORKBENCH_DIR/"}"
    _path="${_path%/steps.sh}"
    _valid_paths["$_path"]=1
  done

  local _entry
  local -a _orphans=()
  while IFS= read -r _entry; do
    [[ -z "$_entry" ]] && continue
    if [[ -z "${_valid_paths[$_entry]:-}" ]]; then
      _orphans+=("$_entry")
    fi
  done <<< "$(state_list)"

  for _entry in "${_orphans[@]}"; do
    state_remove "$_entry"
    warn "Pruned orphaned state entry: $_entry (no matching component)"
  done
}

# state_detect_installed — detects currently installed components and records them.
# Uses heuristics (config files, symlinks, directories) to determine what is present.
# Called by the initial-state migration and by `otto-workbench discover regenerate`.
state_detect_installed() {
  # Docker — detect by state symlink presence
  if [[ -L "$DOCKER_RUNTIME_ALIASES" ]]; then
    state_record "docker"
    # Enrich with runtime choice from symlink target
    local _target _runtime
    _target=$(readlink "$DOCKER_RUNTIME_ALIASES" 2>/dev/null || true)
    if [[ "$_target" == *"/docker/"*"/aliases.zsh" ]]; then
      _runtime="${_target%/aliases.zsh}"
      _runtime="${_runtime##*/}"
      state_set "docker.runtime" "$_runtime"
    fi
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

# ─── Rich state accessors ───────────────────────────────────────────────────

# state_set KEY VALUE — sets an arbitrary YAML path under components.
# Example: state_set "docker.runtime" "orbstack"
state_set() {
  local key="$1" value="$2"
  _state_ensure_yml
  v="$value" yq -i '.components.'"$key"' = strenv(v)' "$INSTALL_YML_FILE"
}

# state_clear_list KEY — resets a YAML list to empty sequence.
state_clear_list() {
  local key="$1"
  _state_ensure_yml
  yq -i '.components.'"$key"' = []' "$INSTALL_YML_FILE"
}

# state_append_list KEY VALUE — appends VALUE to a YAML list (idempotent).
# Example: state_append_list "brew.stacks" "infra/kubernetes"
state_append_list() {
  local key="$1" value="$2"
  _state_ensure_yml
  v="$value" yq -i '.components.'"$key"' |= ((. // []) + [strenv(v)] | unique)' "$INSTALL_YML_FILE"
}

# state_get KEY — reads a YAML value. Returns empty string for missing/null keys.
state_get() {
  [[ -f "$INSTALL_YML_FILE" ]] || return 0
  local val
  val=$(yq '.components.'"$1"' // ""' "$INSTALL_YML_FILE" 2>/dev/null)
  if [[ -n "$val" ]]; then echo "$val"; fi
}

# state_get_list KEY — reads a YAML list, one item per line.
state_get_list() {
  [[ -f "$INSTALL_YML_FILE" ]] || return 0
  yq '.components.'"$1"' | (. // []) | .[]' "$INSTALL_YML_FILE" 2>/dev/null || true
}

# _state_has_new_items SAVED_ARRAY AVAILABLE_ARRAY NEW_ARRAY
# Compares two arrays and populates NEW_ARRAY with items in AVAILABLE but not SAVED.
# Returns 0 if new items were found, 1 otherwise.
_state_has_new_items() {
  local -n __saved=$1 __avail=$2 __new=$3
  __new=()
  local _a _found _s
  for _a in "${__avail[@]}"; do
    _found=false
    for _s in "${__saved[@]}"; do
      if [[ "$_s" == "$_a" ]]; then _found=true; break; fi
    done
    if [[ "$_found" == false ]]; then __new+=("$_a"); fi
  done
  [[ ${#__new[@]} -gt 0 ]]
}

# state_load_selections STATE_KEY SCRIPT_DIR RESULT_ARRAY [AVAILABLE_ARRAY]
# Loads saved selections from YAML, validates each against SCRIPT_DIR.
# When AVAILABLE_ARRAY is provided, detects new tools on disk that aren't in
# the saved list — forces a fresh menu so the user can opt in (or deselect).
# Returns 0 (replaying) if valid saved selections found with no drift.
# Returns 1 (fresh) and clears the list if interactive, no valid saves, or drift detected.
state_load_selections() {
  local state_key="$1" script_dir="$2"
  local -n __selections=$3
  __selections=()

  local _has_available=false
  if [[ $# -ge 4 ]]; then
    local -n __available=$4
    _has_available=true
  fi

  local _saved
  _saved=$(state_get_list "$state_key")

  # No saved state or interactive mode — force fresh selection
  if [[ -z "$_saved" ]] || [[ "${WORKBENCH_INTERACTIVE:-}" == "1" ]]; then
    state_clear_list "$state_key"
    return 1
  fi

  local _item
  while IFS= read -r _item; do
    if [[ -d "$script_dir/$_item" ]]; then __selections+=("$_item"); fi
  done <<< "$_saved"

  # All saved items gone from disk — force fresh selection
  if [[ ${#__selections[@]} -eq 0 ]]; then
    state_clear_list "$state_key"
    return 1
  fi

  # Drift detection: new tools on disk that aren't in saved state
  local _new_tools=()
  if [[ "$_has_available" == true ]] && _state_has_new_items __selections __available _new_tools; then
    info "New tools available: ${_new_tools[*]}"
    __selections=()
    state_clear_list "$state_key"
    return 1
  fi

  info "Using saved selections: ${__selections[*]}"
  return 0
}
