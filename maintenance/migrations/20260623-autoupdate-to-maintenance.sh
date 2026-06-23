#!/usr/bin/env bash
# Migration: swap com.otto-workbench.autoupdate → com.otto-workbench.maintenance.
# Unloads the old launchd agent, removes its plist, renames the state file,
# and installs the new maintenance agent with the same interval.
# Idempotent — no-op if the old agent is already gone.

migration_20260623_autoupdate_to_maintenance() {
  local old_label="com.otto-workbench.autoupdate"
  local old_plist="$HOME/Library/LaunchAgents/${old_label}.plist"
  local state_dir="${XDG_CONFIG_HOME:-$HOME/.config}/workbench"
  local old_state="$state_dir/autoupdate.last"
  local new_state="$state_dir/maintenance.last"

  # Track whether the old agent was running before we remove it
  local was_running=false
  local interval=""
  if launchctl list "$old_label" >/dev/null 2>&1; then
    was_running=true
  fi

  # Preserve the interval from the old agent before removing it
  if [[ -f "$old_plist" ]]; then
    interval=$(/usr/libexec/PlistBuddy -c "Print :StartInterval" "$old_plist" 2>/dev/null || true)
  fi

  # Unload and remove the old agent
  if [[ "$was_running" == true ]]; then
    info "Unloading old autoupdate agent..."
    launchctl unload "$old_plist" 2>/dev/null || true
  fi
  if [[ -f "$old_plist" ]]; then
    rm -f "$old_plist"
    info "Removed old autoupdate plist"
  fi

  # Rename state file
  if [[ -f "$old_state" ]] && [[ ! -f "$new_state" ]]; then
    mv "$old_state" "$new_state"
    info "Renamed autoupdate.last → maintenance.last"
  fi

  # Only start the new agent if the old one was running
  if [[ "$was_running" == true ]]; then
    if [[ -n "$interval" ]]; then
      "$WORKBENCH_DIR/bin/otto-workbench" maintenance start "$interval"
    else
      "$WORKBENCH_DIR/bin/otto-workbench" maintenance start
    fi
  fi
}
