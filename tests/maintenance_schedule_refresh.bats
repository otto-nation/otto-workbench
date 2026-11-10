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
  sed -e "s|__INTERVAL__|7200|g" \
    "$REPO_ROOT/maintenance/systemd/otto-workbench-maintenance.timer.template" > "$TIMER"
  _refresh linux-gnu
  [ "$status" -eq 0 ]
  grep -q "$STABLE/maintenance/bin/otto-workbench-maintenance" "$SERVICE"
  grep -qx 'OnUnitActiveSec=7200sec' "$TIMER"
  grep -q "systemctl --user enable --now otto-workbench-maintenance.timer" "$CALLS"
}

@test "systemd units that match their templates are left alone" {
  sed -e "s|__WORKBENCH_DIR__|$STABLE|g" \
    "$REPO_ROOT/maintenance/systemd/otto-workbench-maintenance.service.template" > "$SERVICE"
  sed -e "s|__INTERVAL__|43200|g" \
    "$REPO_ROOT/maintenance/systemd/otto-workbench-maintenance.timer.template" > "$TIMER"
  _refresh linux-gnu
  [ "$status" -eq 0 ]
  [ ! -e "$CALLS" ]
}

# Sync is what reaches a machine whose repo moved; the refresh does nothing unless
# cmd_sync calls it. Read the same way tests/sync_git_no_prompt.bats reads it.
@test "cmd_sync calls the refresh" {
  run awk '$0 == "cmd_sync() {" {f=1} f&&/^\}/{exit} f' "$REPO_ROOT/bin/otto-workbench"
  [ "$status" -eq 0 ]
  grep -qx '  maintenance_refresh_schedule' <<<"$output"
}
