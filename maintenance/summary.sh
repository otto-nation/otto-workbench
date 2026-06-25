#!/usr/bin/env bash
# Post-install summary for the maintenance component.
# Sourced by install.sh after all components run — defines print_maintenance_summary().

_maintenance_summary_launchd() {
  local _label="com.otto-workbench.maintenance"
  launchctl list "$_label" >/dev/null 2>&1 || return 1
  local _plist="$HOME/Library/LaunchAgents/${_label}.plist"
  local _interval
  _interval=$(/usr/libexec/PlistBuddy -c "Print :StartInterval" "$_plist" 2>/dev/null || echo "unknown")
  local _fmt="$_interval"
  if (( _interval >= 3600 )); then
    _fmt="$(( _interval / 3600 ))h"
  elif (( _interval >= 60 )); then
    _fmt="$(( _interval / 60 ))m"
  fi
  summary_ok "running ${DIM}(every $_fmt)${NC}"
}

print_maintenance_summary() {
  summary_section "Maintenance"

  local _running=false
  if [[ "$OSTYPE" == "darwin"* ]]; then
    _maintenance_summary_launchd && _running=true
  elif systemctl --user is-active otto-workbench-maintenance.timer >/dev/null 2>&1; then
    summary_ok "running ${DIM}(systemd timer)${NC}"
    _running=true
  fi

  if [[ "$_running" == false ]]; then
    summary_warn "not running — start with: ${DIM}otto-workbench maintenance start${NC}"
  fi
}
