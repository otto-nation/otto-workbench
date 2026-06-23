#!/usr/bin/env bash
# Post-install summary for the maintenance component.
# Sourced by install.sh after all components run — defines print_maintenance_summary().

print_maintenance_summary() {
  local _label="com.otto-workbench.maintenance"

  summary_section "Maintenance"

  if launchctl list "$_label" >/dev/null 2>&1; then
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
  else
    summary_warn "not running — start with: ${DIM}otto-workbench maintenance start${NC}"
  fi
}
