#!/usr/bin/env bats
# Tests for the maintenance timer re-render migration.
#
# The failure it fixes has no runtime symptom: a timer that never re-arms after a
# user-manager restart just stops running maintenance. So what is held here is
# that the template carries the arming key and that the migration gets it onto an
# installed timer with the interval intact.

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  UNIT_DIR="$HOME/.config/systemd/user"
  TIMER="$UNIT_DIR/otto-workbench-maintenance.timer"
  MIGRATION="$REPO_ROOT/maintenance/migrations/20261107-maintenance-timer-startup.sh"
  TEMPLATE="$REPO_ROOT/maintenance/systemd/otto-workbench-maintenance.timer.template"
  mkdir -p "$UNIT_DIR"

  # A stand-in workbench whose `maintenance start` records its arguments, so the
  # test never touches the real systemd.
  FAKE_WB="$TMPDIR/wb"
  mkdir -p "$FAKE_WB/bin"
  printf '#!/usr/bin/env bash\necho "$@" > "%s/args"\n' "$TMPDIR" > "$FAKE_WB/bin/otto-workbench"
  chmod +x "$FAKE_WB/bin/otto-workbench"
}

teardown() {
  common_teardown
}

# _run_migration [UNAME] — sources the migration the way lib/migrations.sh does.
# A fake `uname` on PATH stands in for the platform.
_run_migration() {
  local os="${1:-Linux}"
  mkdir -p "$TMPDIR/bin"
  printf '#!/usr/bin/env bash\necho %s\n' "$os" > "$TMPDIR/bin/uname"
  chmod +x "$TMPDIR/bin/uname"
  PATH="$TMPDIR/bin:$PATH" WORKBENCH_DIR="$FAKE_WB" run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    . '$MIGRATION'
    migration_20261107_maintenance_timer_startup
  "
}

_old_timer() {
  printf '[Timer]\nOnBootSec=5min\nOnUnitActiveSec=%ssec\nPersistent=true\n' "$1" > "$TIMER"
}

@test "the template no longer arms from the machine's boot" {
  ! grep -q '^OnBootSec=' "$TEMPLATE"
}

@test "an old timer is re-installed with its own interval" {
  _old_timer 7200
  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$TMPDIR/args")" = "maintenance start 7200" ]
}

@test "an old timer with no readable interval is re-installed with the default" {
  printf '[Timer]\nOnBootSec=5min\n' > "$TIMER"
  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$TMPDIR/args")" = "maintenance start" ]
}

@test "a timer that already has the key is left alone" {
  printf '[Timer]\nOnStartupSec=5min\nOnUnitActiveSec=7200sec\n' > "$TIMER"
  _run_migration
  [ "$status" -eq 3 ]
  [ ! -f "$TMPDIR/args" ]
}

@test "a timer already on OnActiveSec is left alone" {
  printf '[Timer]\nOnActiveSec=5min\nOnUnitActiveSec=7200sec\n' > "$TIMER"
  _run_migration
  [ "$status" -eq 3 ]
  [ ! -f "$TMPDIR/args" ]
}

@test "a timer already on OnCalendar is left alone" {
  printf '[Timer]\nOnCalendar=*-*-* 00/12:00:00\nPersistent=true\n' > "$TIMER"
  _run_migration
  [ "$status" -eq 3 ]
  [ ! -f "$TMPDIR/args" ]
}

@test "no installed timer is a no-op" {
  _run_migration
  [ "$status" -eq 3 ]
  [ ! -f "$TMPDIR/args" ]
}

@test "macOS is a no-op" {
  _old_timer 7200
  _run_migration Darwin
  [ "$status" -eq 3 ]
  [ ! -f "$TMPDIR/args" ]
}
