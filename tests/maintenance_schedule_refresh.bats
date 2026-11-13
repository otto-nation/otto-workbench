#!/usr/bin/env bats
# Tests for maintenance_refresh_schedule (lib/maintenance.sh) — sync re-installs
# an installed maintenance schedule that no longer matches its template.
#
# The failure it fixes has no symptom of its own: a repo that moved left the
# launchd plist naming a script that no longer exists, launchd exited 78 on every
# run, and maintenance silently stopped. launchctl and systemctl are stubs that
# record their calls, so nothing here touches the real scheduler.
bats_require_minimum_version 1.5.0
# select-on: lib/maintenance.sh maintenance/maintenance.plist.template maintenance/systemd/*.template bin/otto-workbench

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.config/systemd/user" "$TMPDIR/bin"
  PLIST="$HOME/Library/LaunchAgents/com.otto-workbench.maintenance.plist"
  TIMER="$HOME/.config/systemd/user/otto-workbench-maintenance.timer"
  SERVICE="$HOME/.config/systemd/user/otto-workbench-maintenance.service"
  CALLS="$TMPDIR/calls"
  STABLE="$TMPDIR/stable-checkout"
  # Each stub records its call. `launchctl list` answers whether the job is
  # loaded: loaded unless LAUNCHCTL_UNLOADED is set.
  local tool
  for tool in launchctl systemctl; do
    printf '#!/usr/bin/env bash\necho "%s $*" >> "%s"\n[[ "$1" == list && -n "${LAUNCHCTL_UNLOADED:-}" ]] && exit 1\nexit 0\n' \
      "$tool" "$CALLS" > "$TMPDIR/bin/$tool"
    chmod +x "$TMPDIR/bin/$tool"
  done
}

teardown() {
  common_teardown
}

# _refresh OSTYPE [ENV...] — sources the library the way bin/otto-workbench does
# and runs the refresh with OSTYPE set to the platform under test.
_refresh() {
  local os="$1"
  shift
  run env "$@" PATH="$TMPDIR/bin:$PATH" WORKBENCH_STABLE_DIR="$STABLE" bash -c '
    export NO_COLOR=1
    WORKBENCH_DIR="$1"
    . "$1/lib/ui.sh"
    . "$1/lib/maintenance.sh"
    OSTYPE="$2"
    maintenance_refresh_schedule
  ' _ "$REPO_ROOT" "$os"
}

# _lib FN [ARGS...] — sources lib/maintenance.sh and runs FN.
_lib() {
  run env HOME="$HOME" NO_COLOR=1 bash -c '
    export NO_COLOR=1
    WORKBENCH_DIR="$1"
    . "$1/lib/ui.sh"
    . "$1/lib/maintenance.sh"
    shift
    "$@"
  ' _ "$REPO_ROOT" "$@"
}

# _linux_start INTERVAL — cmd_maintenance_start with OSTYPE forced to Linux.
_linux_start() {
  run env PATH="$TMPDIR/bin:$PATH" HOME="$HOME" WORKBENCH_STABLE_DIR="$STABLE" bash -c '
    export NO_COLOR=1
    WORKBENCH_DIR="$1"
    . "$1/lib/ui.sh"
    . "$1/lib/maintenance.sh"
    OSTYPE=linux-gnu
    eval "$(sed -n "/^cmd_maintenance_start() {/,/^cmd_maintenance_stop() {/{ /^cmd_maintenance_stop() {/d; p; }" "$1/bin/otto-workbench")"
    cmd_maintenance_start "$2"
  ' _ "$REPO_ROOT" "$1"
}

# _plist PROGRAM_DIR INTERVAL — an installed plist as an older install wrote it.
_plist() {
  sed -e "s|__WORKBENCH_DIR__|$1|g" -e "s|__INTERVAL__|$2|g" \
    -e "s|__LOG_DIR__|$HOME/Library/Logs/otto-workbench|g" \
    "$REPO_ROOT/maintenance/maintenance.plist.template" > "$PLIST"
}

@test "a plist naming a moved checkout is re-rendered for the stable one and reloaded" {
  _plist "/Users/someone/old/place" 3600
  _refresh darwin22
  [ "$status" -eq 0 ]
  [[ "$output" == *"Maintenance schedule re-installed"* ]]
  grep -q "<string>$STABLE/maintenance/bin/otto-workbench-maintenance</string>" "$PLIST"
  # The interval the machine chose survives the re-render.
  grep -A1 '<key>StartInterval</key>' "$PLIST" | grep -q '<integer>3600</integer>'
  grep -q "launchctl load $PLIST" "$CALLS"
  run ! grep -q "/old/place" "$PLIST"
}

@test "a plist that already matches its template is left alone" {
  _plist "$STABLE" 43200
  _refresh darwin22
  [ "$status" -eq 0 ]
  [ ! -e "$CALLS" ]
  [ -z "$output" ]
}

@test "no plist installed means no schedule is created" {
  _refresh darwin22
  [ "$status" -eq 0 ]
  [ ! -e "$PLIST" ]
  [ ! -e "$CALLS" ]
}

# A plist launchd has not loaded is a schedule the operator is not running.
@test "a stale plist launchd has not loaded is corrected but not started" {
  _plist "/Users/someone/old/place" 43200
  _refresh darwin22 LAUNCHCTL_UNLOADED=1
  [ "$status" -eq 0 ]
  [[ "$output" == *"(not loaded)"* ]]
  grep -q "<string>$STABLE/maintenance/bin/otto-workbench-maintenance</string>" "$PLIST"
  run ! grep -q "launchctl load" "$CALLS"
}

# Reloading the job from inside it would end the maintenance run in progress.
@test "inside the launchd job itself the plist is not reloaded" {
  _plist "/Users/someone/old/place" 43200
  _refresh darwin22 XPC_SERVICE_NAME=com.otto-workbench.maintenance
  [ "$status" -eq 0 ]
  grep -q "/old/place" "$PLIST"
  [ ! -e "$CALLS" ]
}

@test "a systemd service naming a moved checkout is re-rendered and restarted" {
  sed -e "s|__WORKBENCH_DIR__|/home/someone/old/place|g" \
    "$REPO_ROOT/maintenance/systemd/otto-workbench-maintenance.service.template" > "$SERVICE"
  printf '[Timer]\nOnActiveSec=5min\nOnUnitActiveSec=7200sec\nPersistent=true\n' > "$TIMER"
  _refresh linux-gnu
  [ "$status" -eq 0 ]
  grep -q "$STABLE/maintenance/bin/otto-workbench-maintenance" "$SERVICE"
  grep -Fqx 'OnCalendar=*-*-* 00/2:00:00' "$TIMER"
  grep -qx "systemctl --user enable otto-workbench-maintenance.timer" "$CALLS"
  grep -qx "systemctl --user restart otto-workbench-maintenance.timer" "$CALLS"
}

@test "the systemd timer template uses a wall-clock schedule" {
  local template="$REPO_ROOT/maintenance/systemd/otto-workbench-maintenance.timer.template"
  grep -qx 'OnCalendar=__CALENDAR__' "$template"
  grep -qx 'RandomizedDelaySec=__DELAY__' "$template"
  grep -qx 'FixedRandomDelay=true' "$template"
  grep -qx 'Persistent=true' "$template"
  ! grep -q '^OnActiveSec=' "$template"
  ! grep -q '^OnUnitActiveSec=' "$template"
  ! grep -q '^On\(Startup\|Boot\)Sec=' "$template"
}

@test "systemd units that match their templates are left alone" {
  sed -e "s|__WORKBENCH_DIR__|$STABLE|g" \
    "$REPO_ROOT/maintenance/systemd/otto-workbench-maintenance.service.template" > "$SERVICE"
  _lib _systemd_render_timer 43200
  [ "$status" -eq 0 ]
  printf '%s\n' "$output" > "$TIMER"
  _refresh linux-gnu
  [ "$status" -eq 0 ]
  [ ! -e "$CALLS" ]
}

@test "_systemd_calendar maps hour and minute intervals" {
  local interval expected
  while IFS=' ' read -r interval expected; do
    [[ -n "$interval" ]] || continue
    _lib _systemd_calendar "$interval"
    [ "$status" -eq 0 ]
    [ "$output" = "$expected" ]
  done <<'EOF'
43200 *-*-* 00/12:00:00
21600 *-*-* 00/6:00:00
86400 *-*-* 00:00:00
3600 *-*-* 00/1:00:00
1800 *-*-* *:00/30:00
900 *-*-* *:00/15:00
EOF
}

@test "_systemd_calendar refuses intervals it cannot express" {
  local interval
  for interval in 5400 25200 45 0 abc; do
    _lib _systemd_calendar "$interval"
    [ "$status" -eq 1 ]
    [ -z "$output" ]
  done
}

@test "_systemd_installed_interval round-trips each rendered form" {
  local interval
  for interval in 43200 21600 86400 3600 1800 900; do
    _lib _systemd_render_timer "$interval"
    [ "$status" -eq 0 ]
    printf '%s\n' "$output" > "$TIMER"
    _lib _systemd_installed_interval
    [ "$status" -eq 0 ]
    [ "$output" = "$interval" ]
  done
}

@test "_systemd_install leaves the installed timer alone when the interval cannot be rendered" {
  printf '[Timer]\nOnCalendar=*-*-* 00/6:00:00\n' > "$TIMER"
  printf '[Service]\nExecStart=/old/place\n' > "$SERVICE"
  _lib _systemd_install 5400
  [ "$status" -eq 1 ]
  [ "$(cat "$TIMER")" = $'[Timer]\nOnCalendar=*-*-* 00/6:00:00' ]
  [ "$(cat "$SERVICE")" = $'[Service]\nExecStart=/old/place' ]
}

@test "_systemd_installed_interval reads a legacy OnUnitActiveSec timer" {
  printf '[Timer]\nOnUnitActiveSec=21600sec\nPersistent=true\n' > "$TIMER"
  _lib _systemd_installed_interval
  [ "$status" -eq 0 ]
  [ "$output" = "21600" ]
}

@test "refresh re-renders a legacy monotonic timer keeping its interval" {
  sed -e "s|__WORKBENCH_DIR__|$STABLE|g" \
    "$REPO_ROOT/maintenance/systemd/otto-workbench-maintenance.service.template" > "$SERVICE"
  printf '[Timer]\nOnActiveSec=5min\nOnUnitActiveSec=21600sec\nPersistent=true\n' > "$TIMER"
  _refresh linux-gnu
  [ "$status" -eq 0 ]
  grep -Fqx 'OnCalendar=*-*-* 00/6:00:00' "$TIMER"
  run ! grep -q '^OnActiveSec=' "$TIMER"
}

@test "maintenance start 5400 on Linux writes no timer" {
  _linux_start 5400
  [ "$status" -ne 0 ]
  [[ "$output" == *"must divide an hour"* ]]
  [ ! -f "$TIMER" ]
  [ ! -e "$CALLS" ]
}

# Sync is what reaches a machine whose repo moved; the refresh does nothing unless
# cmd_sync calls it. Read the same way tests/sync_git_no_prompt.bats reads it.
@test "cmd_sync calls the refresh" {
  run awk '$0 == "cmd_sync() {" {f=1} f&&/^\}/{exit} f' "$REPO_ROOT/bin/otto-workbench"
  [ "$status" -eq 0 ]
  grep -qx '  maintenance_refresh_schedule' <<<"$output"
}
