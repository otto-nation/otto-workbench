#!/usr/bin/env bash
# The otto-queue worker's supervisor units — a launchd agent on macOS; a systemd
# user service and the workbench.slice its jobs run in on Linux. The worker is
# `otto-queue worker` (ai/lib/otto_queue/worker.py): `otto-queue submit` starts
# it on demand and it exits when idle.
#
# Sourced by `bin/otto-workbench` after `lib/maintenance.sh`, whose stable
# checkout (`_maintenance_run_dir`), user unit directory and log directory it
# shares. Unit names match `ai/lib/otto_queue/supervisor.py`.

[[ -n "${_LIB_QUEUE_WORKER_SH:-}" ]] && return
_LIB_QUEUE_WORKER_SH=1

if [[ -z "${_LIB_MAINTENANCE_SH:-}" ]]; then
  echo "ERROR: lib/queue_worker.sh requires lib/maintenance.sh" >&2
  return 1 2>/dev/null || exit 1
fi

_QUEUE_WORKER_LABEL="com.otto-workbench.queue"
_QUEUE_WORKER_PLIST="$HOME/Library/LaunchAgents/${_QUEUE_WORKER_LABEL}.plist"
_QUEUE_WORKER_UNIT="otto-queue-worker.service"
_QUEUE_SLICE_UNIT="workbench.slice"

# _queue_worker_render TEMPLATE — prints TEMPLATE for the stable checkout and log directory.
_queue_worker_render() {
  sed -e "s|__WORKBENCH_DIR__|$(_maintenance_run_dir)|g" \
    -e "s|__LOG_DIR__|${_MAINTENANCE_LOG_DIR_MACOS}|g" "$1"
}

# _queue_worker_write TEMPLATE DEST — replaces DEST with the rendered TEMPLATE
# when they differ, through a temp file. Returns 1 when DEST already matched,
# 2 when the write failed (any leftover DEST.tmp is removed).
_queue_worker_write() {
  local template="$1" dest="$2"
  [[ -f "$dest" ]] && _queue_worker_render "$template" | cmp -s - "$dest" && return 1
  mkdir -p "$(dirname "$dest")" || { rm -f "$dest.tmp"; return 2; }
  if ! _queue_worker_render "$template" > "$dest.tmp"; then
    rm -f "$dest.tmp"
    return 2
  fi
  if ! mv "$dest.tmp" "$dest"; then
    rm -f "$dest.tmp"
    return 2
  fi
}

# _queue_worker_apply TEMPLATE DEST — writes DEST from TEMPLATE. Warns and
# returns 2 on a failed write; returns 1 when DEST already matched.
_queue_worker_apply() {
  local rc=0
  _queue_worker_write "$1" "$2" || rc=$?
  if [[ $rc -eq 2 ]]; then
    warn "Could not write the otto-queue worker unit"
    return 2
  fi
  return "$rc"
}

# _queue_worker_launchd_busy — true when the worker is running, or this is its own job.
_queue_worker_launchd_busy() {
  local listed
  [[ "${XPC_SERVICE_NAME:-}" == "$_QUEUE_WORKER_LABEL" ]] && return 0
  listed="$(launchctl list "$_QUEUE_WORKER_LABEL" 2>/dev/null)" || return 1
  grep -q '"PID" = ' <<<"$listed"
}

# _queue_worker_refresh_unloaded TEMPLATE — a plist launchd has not loaded is
# the operator's choice: re-render it if it differs, but do not start it. A
# missing plist is installed and loaded.
_queue_worker_refresh_unloaded() {
  local template="$1"
  if [[ -f "$_QUEUE_WORKER_PLIST" ]]; then
    _queue_worker_apply "$template" "$_QUEUE_WORKER_PLIST" && \
      success "otto-queue worker file re-rendered for $(_maintenance_run_dir) (not loaded)"
    return 0
  fi
  _queue_worker_apply "$template" "$_QUEUE_WORKER_PLIST" || return 0
  launchctl load "$_QUEUE_WORKER_PLIST" \
    || { warn "Could not load the otto-queue worker — jobs run as tickets; run: launchctl load $_QUEUE_WORKER_PLIST"; return 0; }
  success "otto-queue worker installed for $(_maintenance_run_dir)"
}

_queue_worker_refresh_launchd() {
  local template="$MAINTENANCE_SRC_DIR/queue-worker.plist.template"
  mkdir -p "$_MAINTENANCE_LOG_DIR_MACOS"
  if ! launchctl list "$_QUEUE_WORKER_LABEL" >/dev/null 2>&1; then
    _queue_worker_refresh_unloaded "$template"
    return 0
  fi
  _queue_worker_render "$template" | cmp -s - "$_QUEUE_WORKER_PLIST" && return 0
  # Unloading a running worker kills it, and it may be running this very sync.
  # The old file stays, so a later sync that finds the worker idle sees the
  # difference again and installs it then.
  if _queue_worker_launchd_busy; then
    info "otto-queue worker is busy; its updated definition installs at a later sync"
    return 0
  fi
  launchctl unload "$_QUEUE_WORKER_PLIST" 2>/dev/null || true
  # A failed write leaves the old plist in place: load it again, or the next
  # sync reads the unloaded agent as the operator's choice and never loads it.
  local rc=0
  _queue_worker_apply "$template" "$_QUEUE_WORKER_PLIST" || rc=$?
  if [[ $rc -eq 2 ]]; then
    launchctl load "$_QUEUE_WORKER_PLIST" 2>/dev/null || true
    return 0
  fi
  launchctl load "$_QUEUE_WORKER_PLIST" \
    || { warn "Could not reload the otto-queue worker — jobs run as tickets; run: launchctl load $_QUEUE_WORKER_PLIST"; return 0; }
  success "otto-queue worker re-installed for $(_maintenance_run_dir)"
  return 0
}

_queue_worker_refresh_systemd() {
  # No user manager (no bus): nothing to install into. `otto-queue submit` runs
  # jobs as tickets there, and says so.
  systemctl --user show-environment >/dev/null 2>&1 || return 0
  local changed=false rc=0
  _queue_worker_apply "$MAINTENANCE_SRC_DIR/systemd/${_QUEUE_WORKER_UNIT}.template" \
    "$_SYSTEMD_USER_DIR/$_QUEUE_WORKER_UNIT" && changed=true || rc=$?
  if [[ $rc -eq 2 ]]; then
    return 0
  fi
  rc=0
  # A failed slice write has warned; a service file already written still
  # needs the reload below, so carry on.
  _queue_worker_apply "$MAINTENANCE_SRC_DIR/systemd/${_QUEUE_SLICE_UNIT}.template" \
    "$_SYSTEMD_USER_DIR/$_QUEUE_SLICE_UNIT" && changed=true || true
  # daemon-reload only: a running worker keeps its old definition until its idle
  # exit, and is never restarted from here (it may be running this sync).
  if [[ "$changed" == true ]]; then
    systemctl --user daemon-reload \
      || { warn "Could not reload systemd user units for the otto-queue worker"; return 0; }
    success "otto-queue worker units installed for $(_maintenance_run_dir)"
  fi
  systemctl --user is-enabled --quiet "$_QUEUE_WORKER_UNIT" 2>/dev/null \
    || systemctl --user enable --quiet "$_QUEUE_WORKER_UNIT" \
    || warn "Could not enable $_QUEUE_WORKER_UNIT — leftover jobs wait for the next submit"
  return 0
}

# queue_worker_refresh — installs the otto-queue worker's supervisor units, or
# brings them to what the templates render for the stable checkout. Called by
# sync. Never starts, stops or restarts a running worker: maintenance runs as a
# queue job, so the sync calling this usually runs inside the worker. Linux:
# writes the service and workbench.slice, reloads the manager and enables the
# service for the boot drain. macOS: loads a missing agent, re-renders an
# unloaded one without starting it, and reloads a changed loaded one only while
# the worker is idle. Non-fatal: always returns 0.
queue_worker_refresh() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    _queue_worker_refresh_launchd
  else
    _queue_worker_refresh_systemd
  fi
  return 0
}
