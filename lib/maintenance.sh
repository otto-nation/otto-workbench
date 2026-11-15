#!/usr/bin/env bash
# Maintenance scheduling on both platforms — launchd agent on macOS, systemd
# user timer on Linux. Records each cleanup cycle and prints it from
# `maintenance status`.
#
# Callers must load lib/config.sh (`wb_write_json_file`, `wb_config_get`) and
# lib/portable.sh (`fs_used_pct`). `bin/otto-workbench` already loads both
# through `ui.sh`; the maintenance script sources them explicitly.
# `lib/discover.sh` reads its globals (`_MAINTENANCE_*`, `_SYSTEMD_*`,
# `_format_interval`).

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

# The schedule runs the stable checkout (the main worktree in a bare-repo
# layout), never the worktree a command happened to run from: a feature
# worktree is deleted once its branch merges, and a schedule pointing into one
# fails from then on with nothing reporting it.
_maintenance_run_dir() {
  echo "${WORKBENCH_STABLE_DIR:-$WORKBENCH_DIR}"
}

# _launchd_render INTERVAL — prints the plist the template gives for INTERVAL.
_launchd_render() {
  local interval="${1:-$_MAINTENANCE_DEFAULT_INTERVAL}"
  sed \
    -e "s|__WORKBENCH_DIR__|$(_maintenance_run_dir)|g" \
    -e "s|__INTERVAL__|${interval}|g" \
    -e "s|__LOG_DIR__|${_MAINTENANCE_LOG_DIR_MACOS}|g" \
    "$MAINTENANCE_SRC_DIR/maintenance.plist.template"
}

_launchd_install() {
  local interval="${1:-$_MAINTENANCE_DEFAULT_INTERVAL}"
  mkdir -p "$(dirname "$_MAINTENANCE_PLIST")" "$_MAINTENANCE_LOG_DIR_MACOS"
  _launchd_render "$interval" > "$_MAINTENANCE_PLIST"
}

# _launchd_installed_interval — the StartInterval of the installed plist, or nothing.
_launchd_installed_interval() {
  sed -n '/<key>StartInterval<\/key>/{n;s/.*<integer>\([0-9][0-9]*\)<\/integer>.*/\1/p;q;}' \
    "$_MAINTENANCE_PLIST" 2>/dev/null
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

# _systemd_calendar INTERVAL — prints the OnCalendar expression for INTERVAL
# seconds. Returns 1 (printing nothing) when INTERVAL cannot be expressed: it
# must be a positive integer that divides an hour in whole minutes or a day in
# whole hours.
_systemd_calendar() {
  local interval="$1" h=0 m=0
  [[ "$interval" =~ ^[1-9][0-9]*$ ]] || return 1

  if (( interval % 3600 == 0 )); then
    h=$((interval / 3600))
  elif (( interval % 60 == 0 )); then
    m=$((interval / 60))
  else
    return 1
  fi

  if (( h == 24 )); then
    echo '*-*-* 00:00:00'
    return 0
  fi
  if (( h > 0 && 24 % h == 0 )); then
    echo "*-*-* 00/${h}:00:00"
    return 0
  fi
  if (( m > 0 && 60 % m == 0 )); then
    echo "*-*-* *:00/${m}:00"
    return 0
  fi
  return 1
}

# _systemd_render_timer INTERVAL — prints the timer unit the template gives for
# INTERVAL. DELAY is INTERVAL/12 seconds. Returns 1 when _systemd_calendar fails.
_systemd_render_timer() {
  local interval="$1" calendar delay
  calendar=$(_systemd_calendar "$interval") || return 1
  delay=$((interval / 12))
  sed \
    -e "s|__CALENDAR__|${calendar}|g" \
    -e "s|__DELAY__|${delay}s|g" \
    "$MAINTENANCE_SRC_DIR/systemd/${_SYSTEMD_UNIT}.timer.template"
}

_systemd_install() {
  local interval="${1:-$_MAINTENANCE_DEFAULT_INTERVAL}"
  local svc_template="$MAINTENANCE_SRC_DIR/systemd/${_SYSTEMD_UNIT}.service.template"

  # Rendered before anything is written: a failed render must leave both
  # installed units as they were, not truncate the timer or half-apply the pair.
  local rendered
  rendered=$(_systemd_render_timer "$interval") || return 1

  mkdir -p "$_SYSTEMD_USER_DIR"

  sed -e "s|__WORKBENCH_DIR__|$(_maintenance_run_dir)|g" \
    "$svc_template" > "$_SYSTEMD_USER_DIR/${_SYSTEMD_UNIT}.service"
  printf '%s\n' "$rendered" > "$_SYSTEMD_USER_DIR/${_SYSTEMD_UNIT}.timer"

  systemctl --user daemon-reload
}

_systemd_start() {
  local interval="$1"
  _systemd_install "$interval" || return 1
  systemctl --user enable "${_SYSTEMD_UNIT}.timer"
  # Restart makes the new definition active even on a timer the old monotonic
  # schedule left elapsed. With a calendar schedule a restart fires at once only
  # when Persistent finds a slot missed since the last trigger.
  systemctl --user restart "${_SYSTEMD_UNIT}.timer"
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
  local trigger
  trigger=$(systemctl --user show -p TriggerUSec "${_SYSTEMD_UNIT}.timer" 2>/dev/null) || true
  interval=$(grep -oP '\d+' <<<"$trigger" || true)
  interval=$(head -1 <<<"$interval")
  if [[ -n "$interval" ]]; then
    echo -e "  ${GREEN}Running${NC}  (systemd timer active)"
  else
    echo -e "  ${GREEN}Running${NC}"
  fi
  return 0
}

# _systemd_installed_interval — seconds encoded in the installed timer's
# OnCalendar line, or the legacy OnUnitActiveSec value, or nothing.
_systemd_installed_interval() {
  local timer="$_SYSTEMD_USER_DIR/${_SYSTEMD_UNIT}.timer" cal
  local hour_re='^\*-\*-\* 00/([0-9]+):00:00$'
  local min_re='^\*-\*-\* \*:00/([0-9]+):00$'
  [[ -f "$timer" ]] || return 0
  cal=$(sed -n '/^OnCalendar=/{s/^OnCalendar=//p;q;}' "$timer" 2>/dev/null) || true
  if [[ "$cal" == '*-*-* 00:00:00' ]]; then
    echo 86400
    return 0
  fi
  if [[ "$cal" =~ $hour_re ]]; then
    echo $((BASH_REMATCH[1] * 3600))
    return 0
  fi
  if [[ "$cal" =~ $min_re ]]; then
    echo $((BASH_REMATCH[1] * 60))
    return 0
  fi
  sed -n '/^OnUnitActiveSec=[0-9][0-9]*sec$/{s/^OnUnitActiveSec=\([0-9]*\)sec$/\1/p;q;}' \
    "$timer" 2>/dev/null
  return 0
}

# _systemd_schedule_current INTERVAL — true when both installed units equal
# what the templates render for INTERVAL.
_systemd_schedule_current() {
  local interval="$1"
  sed -e "s|__WORKBENCH_DIR__|$(_maintenance_run_dir)|g" \
    "$MAINTENANCE_SRC_DIR/systemd/${_SYSTEMD_UNIT}.service.template" \
    | cmp -s - "$_SYSTEMD_USER_DIR/${_SYSTEMD_UNIT}.service" || return 1
  _systemd_render_timer "$interval" \
    | cmp -s - "$_SYSTEMD_USER_DIR/${_SYSTEMD_UNIT}.timer"
}

# maintenance_refresh_schedule — re-installs an installed maintenance schedule
# whose files no longer match what the templates render, keeping its interval.
# A repo that moved leaves the schedule naming a path that no longer exists, and
# a template change otherwise reaches a machine only through `maintenance
# start`. Called by sync. A machine with no schedule installed is left without
# one, and a plist launchd has not loaded is corrected but not started. Skipped
# when sync runs inside the launchd job itself: reloading the job would end the
# run in progress, and a job that is running found its program.
# Non-fatal: always returns 0.
maintenance_refresh_schedule() {
  local interval
  if [[ "$OSTYPE" == "darwin"* ]]; then
    [[ -f "$_MAINTENANCE_PLIST" ]] || return 0
    [[ "${XPC_SERVICE_NAME:-}" == "$_MAINTENANCE_LABEL" ]] && return 0
    interval=$(_launchd_installed_interval)
    _launchd_render "${interval:-$_MAINTENANCE_DEFAULT_INTERVAL}" | cmp -s - "$_MAINTENANCE_PLIST" && return 0
    # A plist on disk that launchd has not loaded is a schedule the operator is
    # not running: correct the file, but do not start it.
    if ! _launchd_is_loaded; then
      _launchd_install "${interval:-$_MAINTENANCE_DEFAULT_INTERVAL}"
      success "Maintenance schedule file re-rendered for $(_maintenance_run_dir) (not loaded)"
      return 0
    fi
    _launchd_start "${interval:-$_MAINTENANCE_DEFAULT_INTERVAL}" \
      || { warn "Could not reload the maintenance schedule — run: otto-workbench maintenance start"; return 0; }
  else
    [[ -f "$_SYSTEMD_USER_DIR/${_SYSTEMD_UNIT}.timer" ]] || return 0
    interval=$(_systemd_installed_interval)
    interval="${interval:-$_MAINTENANCE_DEFAULT_INTERVAL}"
    if ! _systemd_calendar "$interval" >/dev/null; then
      interval="$_MAINTENANCE_DEFAULT_INTERVAL"
    fi
    _systemd_schedule_current "$interval" && return 0
    _systemd_start "$interval" \
      || { warn "Could not reload the maintenance schedule — run: otto-workbench maintenance start"; return 0; }
  fi
  success "Maintenance schedule re-installed for $(_maintenance_run_dir)"
  return 0
}

# _maintenance_queue_log — prints the log_path of the newest otto-queue entry
# whose key is maintenance. Empty when otto-queue is missing, errors, or has
# no such entry.
_maintenance_queue_log() {
  local queue="${OTTO_QUEUE_BIN:-$WORKBENCH_DIR/ai/bin/otto-queue}" json
  [[ -x "$queue" ]] || return 0
  json=$("$queue" status --json 2>/dev/null) || return 0
  [[ -n "$json" ]] || return 0
  python3 -c '
import json, sys
best_id, path = -1, ""
for raw in sys.stdin.read().splitlines():
    raw = raw.strip()
    if not raw:
        continue
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        continue
    if entry.get("idem_key") != "maintenance":
        continue
    log = entry.get("log_path") or ""
    ident = entry.get("id")
    if not log or not isinstance(ident, int):
        continue
    if ident > best_id:
        best_id, path = ident, log
if path:
    print(path)
' <<<"$json"
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

# _maintenance_date EPOCH — prints EPOCH as `YYYY-MM-DD HH:MM:SS`, or `unknown`.
_maintenance_date() {
  local epoch="$1"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    date -r "$epoch" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "unknown"
  else
    date -d "@$epoch" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "unknown"
  fi
}

# maintenance_cleanup_record SWEEP_LINE PRUNE_LINE — records one cleanup cycle
# and prints `Cleanup: SWEEP_LINE PRUNE_LINE`.
#
# Writes `{at,line}` to `$MAINTENANCE_CLEANUP_FILE` and trails a `cleanup`
# event. Every side effect is non-fatal: the cycle already finished.
maintenance_cleanup_record() {
  local sweep_line="$1" prune_line="$2"
  local summary json blob repos removed pressure
  summary="Cleanup: $sweep_line $prune_line"

  json=$(jq -n --arg line "$summary" --argjson at "$(date +%s)" '{at:$at, line:$line}') || true
  # The cycle already finished; a status write must not fail it.
  [[ -z "$json" ]] || wb_write_json_file "$MAINTENANCE_CLEANUP_FILE" "$json" || true

  blob="$sweep_line $prune_line"
  repos="${blob##*repos=}"
  repos="${repos%% *}"
  removed="${blob##*removed=}"
  removed="${removed%% *}"
  pressure="${blob##*pressure=}"
  pressure="${pressure%% *}"

  # A missing otto-log must not fail the cycle.
  "$WORKBENCH_DIR/ai/bin/otto-log" record \
    --script maintenance --action cleanup --detail "$summary" \
    --data "repos=$repos" --data "removed=$removed" --data "pressure=$pressure" \
    > /dev/null || true

  echo "$summary"
  return 0
}

# maintenance_cleanup_status — prints home disk use and the last cleanup line.
#
# Disk percent is `fs_used_pct "$HOME"`; the prune-pressure limit is the
# global `maintenance.disk_pct` (read from `/` so no project scope applies).
# An unreadable state file prints `Last cleanup: unknown`; a missing file
# prints no last-cleanup line.
maintenance_cleanup_status() {
  local pct limit at line last_date
  pct=$(fs_used_pct "$HOME") || pct=
  limit=$(cd / && wb_config_get "$MAINTENANCE_DISK_PCT_CONFIG_KEY" 85) || limit=85
  echo "  Home filesystem: ${pct}% used (prune pressure at ${limit}%)"
  [[ -f "$MAINTENANCE_CLEANUP_FILE" ]] || return 0
  at=$(jq -r '.at // empty' "$MAINTENANCE_CLEANUP_FILE" 2>/dev/null) || at=
  line=$(jq -r '.line // empty' "$MAINTENANCE_CLEANUP_FILE" 2>/dev/null) || line=
  if [[ -z "$at" || -z "$line" ]]; then
    echo "  Last cleanup: unknown"
    return 0
  fi
  last_date=$(_maintenance_date "$at")
  echo "  Last cleanup: $last_date — $line"
  return 0
}
