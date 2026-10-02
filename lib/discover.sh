#!/usr/bin/env bash
# Discover printers for installed components, registered scripts, and
# scheduled agents (launchd on macOS, systemd user timers on Linux).
#
# Sourced by `bin/otto-workbench`. Reads `_format_interval`, `_SYSTEMD_USER_DIR`,
# and `_MAINTENANCE_*` from `lib/maintenance.sh`.

[[ -n "${_LIB_DISCOVER_SH:-}" ]] && return
_LIB_DISCOVER_SH=1

# Guard: constants must be loaded
if [[ -z "${WORKBENCH_DIR:-}" ]]; then
  echo "ERROR: lib/discover.sh requires WORKBENCH_DIR (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

_discover_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=maintenance.sh
. "$_discover_lib_dir/maintenance.sh"
unset _discover_lib_dir

_discover_components() {
  if ! state_file_exists; then
    echo -e "    ${DIM}(no state file — run 'otto-workbench discover regenerate' to detect)${NC}"
    return
  fi
  if [[ ! -f "$INSTALL_YML_FILE" ]]; then
    echo -e "    ${DIM}(legacy state file — run 'otto-workbench sync' to migrate)${NC}"
    return
  fi
  local comp val
  while IFS= read -r comp; do
    [[ -z "$comp" ]] && continue
    echo "    $comp"
    val=$(c="$comp" yq -r '.components[env(c)]' "$INSTALL_YML_FILE" 2>/dev/null)
    [[ "$val" == "true" ]] && continue
    c="$comp" yq -r '.components[env(c)] | to_entries[] | "      " + .key + ": " + (.value | tostring)' "$INSTALL_YML_FILE" 2>/dev/null
  done < <(yq '.components | keys | .[]' "$INSTALL_YML_FILE" 2>/dev/null)
}

_discover_scripts() {
  local registry="$BIN_REGISTRY_FILE"
  if [[ ! -f "$registry" ]] || ! command -v yq &>/dev/null; then
    echo -e "    ${DIM}(registry not found or yq not installed)${NC}"
    return
  fi
  local count name desc
  count=$(yq '.tools | length' "$registry")
  for (( i = 0; i < count; i++ )); do
    name=$(yq ".tools[$i].name" "$registry")
    desc=$(yq ".tools[$i].description" "$registry")
    printf "    ${GREEN}%-18s${NC} %s\n" "$name" "$desc"
  done
}

_discover_launchd_agents() {
  local found=false plist label short interval
  for plist in "$HOME"/Library/LaunchAgents/com.otto-workbench.*.plist; do
    [[ -f "$plist" ]] || continue
    found=true
    label=$(/usr/libexec/PlistBuddy -c "Print :Label" "$plist" 2>/dev/null)
    short="${label#com.otto-workbench.}"
    if launchctl list "$label" &>/dev/null; then
      interval=$(/usr/libexec/PlistBuddy -c "Print :StartInterval" "$plist" 2>/dev/null || echo "")
      [[ -n "$interval" ]] \
        && printf "    ${GREEN}%-18s${NC} Running (every %s)\n" "$short" "$(_format_interval "$interval")" \
        || printf "    ${GREEN}%-18s${NC} Running\n" "$short"
    else
      printf "    ${DIM}%-18s Stopped${NC}\n" "$short"
    fi
  done
  [[ "$found" == true ]]
}

_discover_systemd_timers() {
  local found=false unit short
  for unit in "$_SYSTEMD_USER_DIR"/otto-workbench-*.timer; do
    [[ -f "$unit" ]] || continue
    found=true
    short=$(basename "$unit" .timer)
    if systemctl --user is-active "$short.timer" &>/dev/null; then
      printf "    ${GREEN}%-18s${NC} Running (systemd timer)\n" "${short#otto-workbench-}"
    else
      printf "    ${DIM}%-18s Stopped${NC}\n" "${short#otto-workbench-}"
    fi
  done
  [[ "$found" == true ]]
}

_discover_agents() {
  local found=false
  if [[ "$OSTYPE" == "darwin"* ]]; then
    if _discover_launchd_agents; then found=true; fi
  else
    if _discover_systemd_timers; then found=true; fi
  fi
  [[ "$found" == false ]] && echo -e "    ${DIM}(none installed)${NC}"
  return 0
}
