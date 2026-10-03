#!/usr/bin/env bash
# Maintenance scheduling on both platforms — launchd agent on macOS, systemd
# user timer on Linux.
#
# Sourced by `bin/otto-workbench`; `lib/discover.sh` reads its globals
# (`_MAINTENANCE_*`, `_SYSTEMD_*`, `_format_interval`).

[[ -n "${_LIB_MAINTENANCE_SH:-}" ]] && return
_LIB_MAINTENANCE_SH=1

# Guard: constants must be loaded
if [[ -z "${WORKBENCH_DIR:-}" ]]; then
  echo "ERROR: lib/maintenance.sh requires WORKBENCH_DIR (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

_MAINTENANCE_DEFAULT_INTERVAL=43200  # 12 hours

# ── macOS (launchd) ──────────────────────────────────────────────────────────

_MAINTENANCE_LABEL="com.otto-workbench.maintenance"
_MAINTENANCE_PLIST="$HOME/Library/LaunchAgents/${_MAINTENANCE_LABEL}.plist"
_MAINTENANCE_LOG_DIR_MACOS="$HOME/Library/Logs/otto-workbench"

_launchd_is_loaded() {
  launchctl list "$_MAINTENANCE_LABEL" >/dev/null 2>&1
}

_launchd_install() {
  local interval="${1:-$_MAINTENANCE_DEFAULT_INTERVAL}"
  local template="$MAINTENANCE_SRC_DIR/maintenance.plist.template"

  mkdir -p "$(dirname "$_MAINTENANCE_PLIST")" "$_MAINTENANCE_LOG_DIR_MACOS"

  sed \
    -e "s|__WORKBENCH_DIR__|${WORKBENCH_DIR}|g" \
    -e "s|__INTERVAL__|${interval}|g" \
    -e "s|__LOG_DIR__|${_MAINTENANCE_LOG_DIR_MACOS}|g" \
    "$template" > "$_MAINTENANCE_PLIST"
}

_launchd_start() {
  local interval="$1"
  if _launchd_is_loaded; then
    launchctl unload "$_MAINTENANCE_PLIST" 2>/dev/null || true
  fi
  _launchd_install "$interval"
  launchctl load "$_MAINTENANCE_PLIST"
}

_launchd_stop() {
  if _launchd_is_loaded; then
    launchctl unload "$_MAINTENANCE_PLIST" 2>/dev/null || true
    rm -f "$_MAINTENANCE_PLIST"
    return 0
  fi
  return 1
}

_launchd_status() {
  if ! _launchd_is_loaded; then return 1; fi
  local interval
  interval=$(/usr/libexec/PlistBuddy -c "Print :StartInterval" "$_MAINTENANCE_PLIST" 2>/dev/null || echo "unknown")
  echo -e "  ${GREEN}Running${NC}  every $(_format_interval "$interval")"
  return 0
}

# ── Linux (systemd user timer) ───────────────────────────────────────────────

_SYSTEMD_UNIT="otto-workbench-maintenance"
_SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

_systemd_is_active() {
  systemctl --user is-active "${_SYSTEMD_UNIT}.timer" >/dev/null 2>&1
}

_systemd_install() {
  local interval="${1:-$_MAINTENANCE_DEFAULT_INTERVAL}"
  local svc_template="$MAINTENANCE_SRC_DIR/systemd/${_SYSTEMD_UNIT}.service.template"
  local tmr_template="$MAINTENANCE_SRC_DIR/systemd/${_SYSTEMD_UNIT}.timer.template"

  mkdir -p "$_SYSTEMD_USER_DIR"

  sed -e "s|__WORKBENCH_DIR__|${WORKBENCH_DIR}|g" \
    "$svc_template" > "$_SYSTEMD_USER_DIR/${_SYSTEMD_UNIT}.service"

  sed -e "s|__INTERVAL__|${interval}|g" \
    "$tmr_template" > "$_SYSTEMD_USER_DIR/${_SYSTEMD_UNIT}.timer"

  systemctl --user daemon-reload
}

_systemd_start() {
  local interval="$1"
  _systemd_install "$interval"
  systemctl --user enable --now "${_SYSTEMD_UNIT}.timer"
}

_systemd_stop() {
  if _systemd_is_active; then
    systemctl --user disable --now "${_SYSTEMD_UNIT}.timer"
    rm -f "$_SYSTEMD_USER_DIR/${_SYSTEMD_UNIT}".{service,timer}
    systemctl --user daemon-reload
    return 0
  fi
  return 1
}

_systemd_status() {
  if ! _systemd_is_active; then return 1; fi
  local interval
  interval=$(systemctl --user show -p TriggerUSec "${_SYSTEMD_UNIT}.timer" 2>/dev/null \
    | grep -oP '\d+' | head -1)
  if [[ -n "$interval" ]]; then
    echo -e "  ${GREEN}Running${NC}  (systemd timer active)"
  else
    echo -e "  ${GREEN}Running${NC}"
  fi
  return 0
}

_format_interval() {
  local seconds=$1
  if (( seconds >= 3600 )); then
    echo "$(( seconds / 3600 ))h"
  elif (( seconds >= 60 )); then
    echo "$(( seconds / 60 ))m"
  else
    echo "${seconds}s"
  fi
}
