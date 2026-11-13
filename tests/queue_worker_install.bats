#!/usr/bin/env bats
# Tests for queue_worker_refresh (lib/queue_worker.sh) — sync installs the
# otto-queue worker's supervisor units and never disturbs a running worker.
#
# The worker usually runs the very sync that refreshes it (maintenance is a
# queue job), so a restart here would kill the run doing the restarting.
# launchctl and systemctl are stubs that record their calls.
bats_require_minimum_version 1.5.0
# select-on: lib/queue_worker.sh maintenance/queue-worker.plist.template maintenance/systemd/otto-queue-worker.service.template maintenance/systemd/workbench.slice.template bin/otto-workbench ai/lib/otto_queue/supervisor.py

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.config/systemd/user" "$TMPDIR/bin"
  PLIST="$HOME/Library/LaunchAgents/com.otto-workbench.queue.plist"
  SERVICE="$HOME/.config/systemd/user/otto-queue-worker.service"
  SLICE="$HOME/.config/systemd/user/workbench.slice"
  export CALLS="$TMPDIR/calls"
  STABLE="$TMPDIR/stable-checkout"
  cat > "$TMPDIR/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS"
case "$2" in
  show-environment) [[ -z "${SYSTEMCTL_NO_MANAGER:-}" ]] ;;
  is-enabled) [[ -n "${SYSTEMCTL_ENABLED:-}" ]] ;;
  *) exit 0 ;;
esac
EOF
  cat > "$TMPDIR/bin/launchctl" <<'EOF'
#!/usr/bin/env bash
echo "launchctl $*" >> "$CALLS"
if [[ "$1" == list ]]; then
  [[ -n "${LAUNCHCTL_UNLOADED:-}" ]] && exit 1
  [[ -n "${LAUNCHCTL_RUNNING:-}" ]] && echo '"PID" = 4242;'
fi
exit 0
EOF
  chmod +x "$TMPDIR/bin/systemctl" "$TMPDIR/bin/launchctl"
}

teardown() {
  common_teardown
}

# _refresh OSTYPE [ENV...] — sources the libraries the way bin/otto-workbench does.
_refresh() {
  local os="$1"
  shift
  run env "$@" PATH="$TMPDIR/bin:$PATH" WORKBENCH_STABLE_DIR="$STABLE" bash -c '
    export NO_COLOR=1
    WORKBENCH_DIR="$1"
    . "$1/lib/ui.sh"
    . "$1/lib/maintenance.sh"
    . "$1/lib/queue_worker.sh"
    OSTYPE="$2"
    queue_worker_refresh
  ' _ "$REPO_ROOT" "$os"
}

# _render TEMPLATE DIR — a template as sync renders it for checkout DIR.
_render() {
  sed -e "s|__WORKBENCH_DIR__|$2|g" -e "s|__LOG_DIR__|$HOME/Library/Logs/otto-workbench|g" "$1"
}

@test "Linux gets the worker service and the slice, enabled and never started" {
  _refresh linux-gnu
  [ "$status" -eq 0 ]
  grep -qF "\"$STABLE/ai/bin/otto-queue\" worker" "$SERVICE"
  grep -qF ". \"$STABLE/zsh/env.sh\" && workbench_env" "$SERVICE"
  grep -q '^KillMode=process$' "$SERVICE"
  grep -q '^MemoryHigh=2G$' "$SLICE"
  grep -q '^MemoryMax=2560M$' "$SLICE"
  grep -q '^MemorySwapMax=0$' "$SLICE"
  grep -q "systemctl --user daemon-reload" "$CALLS"
  grep -q "systemctl --user enable --quiet otto-queue-worker.service" "$CALLS"
  run ! grep -Eq 'systemctl --user (start|restart|stop|reload)' "$CALLS"
}

@test "Linux units that already match are left alone" {
  _render "$REPO_ROOT/maintenance/systemd/otto-queue-worker.service.template" "$STABLE" > "$SERVICE"
  cp "$REPO_ROOT/maintenance/systemd/workbench.slice.template" "$SLICE"
  _refresh linux-gnu SYSTEMCTL_ENABLED=1
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  run ! grep -q "daemon-reload" "$CALLS"
}

@test "Linux with no user manager installs nothing" {
  _refresh linux-gnu SYSTEMCTL_NO_MANAGER=1
  [ "$status" -eq 0 ]
  [ ! -e "$SERVICE" ]
  [ ! -e "$SLICE" ]
}

@test "a Mac with no agent gets it written and loaded" {
  _refresh darwin22 LAUNCHCTL_UNLOADED=1
  [ "$status" -eq 0 ]
  grep -qF "\"$STABLE/ai/bin/otto-queue\" worker" "$PLIST"
  grep -q "launchctl load $PLIST" "$CALLS"
}

@test "a changed agent is not reloaded while the worker runs" {
  _render "$REPO_ROOT/maintenance/queue-worker.plist.template" "/old/place" > "$PLIST"
  _refresh darwin22 LAUNCHCTL_RUNNING=1
  [ "$status" -eq 0 ]
  run ! grep -q "launchctl unload" "$CALLS"
  grep -q "/old/place" "$PLIST"
}

@test "a changed agent is not reloaded from inside the worker" {
  _render "$REPO_ROOT/maintenance/queue-worker.plist.template" "/old/place" > "$PLIST"
  _refresh darwin22 XPC_SERVICE_NAME=com.otto-workbench.queue
  [ "$status" -eq 0 ]
  run ! grep -q "launchctl unload" "$CALLS"
}

@test "a changed agent is reloaded when the worker is idle" {
  _render "$REPO_ROOT/maintenance/queue-worker.plist.template" "/old/place" > "$PLIST"
  _refresh darwin22
  [ "$status" -eq 0 ]
  grep -q "launchctl unload $PLIST" "$CALLS"
  grep -q "launchctl load $PLIST" "$CALLS"
  run ! grep -q "/old/place" "$PLIST"
}

@test "the unit names match what the worker's supervisor starts" {
  local py sh_names label
  py="$(PYTHONPATH="$REPO_ROOT/ai/lib${PYTHONPATH:+:$PYTHONPATH}" python3 -c \
    'import otto_queue.supervisor as s; print(s.SYSTEMD_UNIT, s.SLICE, s.LAUNCHD_LABEL)')"
  sh_names="$(
    export NO_COLOR=1 WORKBENCH_DIR="$REPO_ROOT"
    bash -c '
      . "$WORKBENCH_DIR/lib/ui.sh"
      . "$WORKBENCH_DIR/lib/maintenance.sh"
      . "$WORKBENCH_DIR/lib/queue_worker.sh"
      printf "%s %s %s\n" "$_QUEUE_WORKER_UNIT" "$_QUEUE_SLICE_UNIT" "$_QUEUE_WORKER_LABEL"
    '
  )"
  [ "$sh_names" = "$py" ]
  label="$(sed -n '/<key>Label<\/key>/{n;s/.*<string>\(.*\)<\/string>.*/\1/p;}' \
    "$REPO_ROOT/maintenance/queue-worker.plist.template")"
  [ "$label" = "${py##* }" ]
}

@test "a failed unit write warns, does not claim re-install, and reloads the old plist" {
  _render "$REPO_ROOT/maintenance/queue-worker.plist.template" "/old/place" > "$PLIST"
  chmod a-w "$(dirname "$PLIST")"
  _refresh darwin22
  chmod u+w "$(dirname "$PLIST")"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Could not write"* ]]
  [[ "$output" != *"re-installed"* ]]
  grep -q "/old/place" "$PLIST"
  grep -q "launchctl load $PLIST" "$CALLS"
  [ ! -e "$PLIST.tmp" ]
}

@test "an unloaded agent is re-rendered but not loaded" {
  _render "$REPO_ROOT/maintenance/queue-worker.plist.template" "/old/place" > "$PLIST"
  _refresh darwin22 LAUNCHCTL_UNLOADED=1
  [ "$status" -eq 0 ]
  [[ "$output" == *"(not loaded)"* ]]
  run ! grep -q "/old/place" "$PLIST"
  run ! grep -q "launchctl load" "$CALLS"
}

@test "the log directory exists before a reload of a changed idle agent" {
  _render "$REPO_ROOT/maintenance/queue-worker.plist.template" "/old/place" > "$PLIST"
  _refresh darwin22
  [ "$status" -eq 0 ]
  [ -d "$HOME/Library/Logs/otto-workbench" ]
}

@test "sync refreshes the worker units after the maintenance schedule" {
  local schedule worker
  schedule="$(grep -n '^  maintenance_refresh_schedule$' "$REPO_ROOT/bin/otto-workbench" | cut -d: -f1)"
  worker="$(grep -n '^  queue_worker_refresh$' "$REPO_ROOT/bin/otto-workbench" | cut -d: -f1)"
  [ -n "$schedule" ] && [ -n "$worker" ]
  [ "$schedule" -lt "$worker" ]
}
