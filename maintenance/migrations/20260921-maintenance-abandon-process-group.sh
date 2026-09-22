#!/usr/bin/env bash
# Migration: re-render the maintenance agent so it carries AbandonProcessGroup.
#
# launchd kills every process sharing a job's process group when the job exits
# (launchd.plist(5)). The maintenance script now spawns headless auto-task
# agents with `& disown`, which leaves them in that group — so without the key
# they are reaped the moment maintenance finishes, having logged that they
# started. The template gained the key; this is what gets it onto a machine that
# installed the agent before it did.
#
# `otto-workbench sync` never re-renders the plist — only `maintenance start`
# does — so nothing else would pick this up. The interval is read back first so
# a machine running a non-default one keeps it.
#
# Idempotent: a plist that already has the key is left alone, and a machine that
# never installed the agent has nothing to re-render.

migration_20260921_maintenance_abandon_process_group() {
  local label="com.otto-workbench.maintenance"
  local plist="$HOME/Library/LaunchAgents/${label}.plist"

  # Linux gets the same fix through the systemd unit template, which
  # `maintenance start` renders the same way. Nothing to convert here.
  [[ "$(uname -s)" == "Darwin" ]] || return "$MIGRATION_NOOP"

  # The agent was never installed on this machine. Not deferred: the installer
  # renders from the current template, so an agent installed later already has
  # the key and there is nothing for this to come back for.
  [[ -f "$plist" ]] || return "$MIGRATION_NOOP"

  if /usr/libexec/PlistBuddy -c "Print :AbandonProcessGroup" "$plist" > /dev/null 2>&1; then
    return "$MIGRATION_NOOP"
  fi

  local interval=""
  interval="$(/usr/libexec/PlistBuddy -c "Print :StartInterval" "$plist" 2>/dev/null || true)"

  info "Re-installing maintenance agent with AbandonProcessGroup"
  if [[ -n "$interval" ]]; then
    "$WORKBENCH_DIR/bin/otto-workbench" maintenance start "$interval"
  else
    "$WORKBENCH_DIR/bin/otto-workbench" maintenance start
  fi
}
