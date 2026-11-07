#!/usr/bin/env bash
# Migration: re-render the systemd maintenance timer so it arms on manager start.
#
# The timer used OnBootSec plus OnUnitActiveSec. The second counts from the
# service's last activation, which a restart of the user manager forgets, and the
# first has elapsed long before — so after such a restart nothing arms the timer
# and maintenance silently stops. The template now uses OnStartupSec, which
# counts from the manager's start; this is what gets it onto a machine that
# installed the timer before it did.
#
# `otto-workbench sync` never re-renders the unit — only `maintenance start`
# does — so nothing else would pick this up. The interval is read back first so
# a machine running a non-default one keeps it.
#
# Idempotent: a timer that already has the key is left alone, and a machine that
# never installed the timer has nothing to re-render.

migration_20261107_maintenance_timer_startup() {
  local timer="$HOME/.config/systemd/user/otto-workbench-maintenance.timer"

  # macOS schedules through launchd, whose StartInterval has no such gap.
  [[ "$(uname -s)" == "Linux" ]] || return "$MIGRATION_NOOP"

  # Not installed here. Not deferred: the installer renders from the current
  # template, so a timer installed later already has the key.
  [[ -f "$timer" ]] || return "$MIGRATION_NOOP"

  if grep -q '^OnStartupSec=' "$timer"; then
    return "$MIGRATION_NOOP"
  fi

  local interval=""
  interval="$(sed -n '/^OnUnitActiveSec=/{s/^OnUnitActiveSec=\([0-9][0-9]*\)sec$/\1/p;q;}' "$timer")"

  info "Re-installing maintenance timer with OnStartupSec"
  if [[ -n "$interval" ]]; then
    "$WORKBENCH_DIR/bin/otto-workbench" maintenance start "$interval"
  else
    "$WORKBENCH_DIR/bin/otto-workbench" maintenance start
  fi
}
