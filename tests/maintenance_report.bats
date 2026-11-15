#!/usr/bin/env bats
# Tests for the cleanup cycle summary: maintenance_cleanup_record writes the
# state file and trail, and otto-workbench maintenance status prints it even
# when the timer is not running.
bats_require_minimum_version 1.5.0
# select-on: lib/maintenance.sh bin/otto-workbench maintenance/bin/otto-workbench-maintenance

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME/.local/bin"
  export HOME="$TEST_HOME"
  gate_sandbox

  # shellcheck source=../lib/constants.sh
  . "$REPO_ROOT/lib/constants.sh"
  # shellcheck source=../lib/config.sh
  . "$REPO_ROOT/lib/config.sh"
  # shellcheck source=../lib/portable.sh
  . "$REPO_ROOT/lib/portable.sh"
  # shellcheck source=../lib/maintenance.sh
  . "$REPO_ROOT/lib/maintenance.sh"

  STUB_BIN="$TEST_HOME/.local/bin"
  SWEEP_LINE="repos=2 removed=3 failed=0"
  PRUNE_LINE="pressure=yes disk_pct=91 go=1.2GB(pressure) mise=0.0GB(pressure) docker=off"
  SUMMARY="Cleanup: $SWEEP_LINE $PRUNE_LINE"
}

teardown() {
  common_teardown
}

# _record — one cleanup cycle against the sandboxed state root.
_record() {
  run maintenance_cleanup_record "$SWEEP_LINE" "$PRUNE_LINE"
}

# _status — otto-workbench maintenance status with the timer not running and
# df reporting 91% used. Copied from the env/PATH shape in
# tests/maintenance_queue.bats (_logs).
_status() {
  printf '#!/usr/bin/env bash\nexit 1\n' > "$STUB_BIN/launchctl"
  printf '#!/usr/bin/env bash\nexit 1\n' > "$STUB_BIN/systemctl"
  cat > "$STUB_BIN/df" <<'EOF'
#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
printf '/dev/x 100 91 9 91%% /\n'
EOF
  chmod +x "$STUB_BIN/launchctl" "$STUB_BIN/systemctl" "$STUB_BIN/df"
  run env HOME="$TEST_HOME" PATH="$STUB_BIN:$PATH" NO_COLOR=1 \
    WORKBENCH_STATE_DIR="$WORKBENCH_STATE_DIR" WORKBENCH_CACHE_DIR="$WORKBENCH_CACHE_DIR" \
    WORKBENCH_CONFIG_DIR="$WORKBENCH_CONFIG_DIR" \
    "$REPO_ROOT/bin/otto-workbench" --workbench-dir "$REPO_ROOT" maintenance status
}

@test "maintenance_cleanup_record writes the state file and a cleanup trail event" {
  _record
  [ "$status" -eq 0 ]
  [ "$output" = "$SUMMARY" ]
  [ "$(jq -r .line "$MAINTENANCE_CLEANUP_FILE")" = "$SUMMARY" ]

  run env HOME="$TEST_HOME" WORKBENCH_STATE_DIR="$WORKBENCH_STATE_DIR" \
    "$REPO_ROOT/ai/bin/otto-log" query --script maintenance --json
  [ "$status" -eq 0 ]
  python3 -c '
import json, sys
events = [json.loads(line) for line in sys.stdin if line.strip()]
hits = [e for e in events if e.get("action") == "cleanup"]
assert hits, events
data = hits[0].get("data") or {}
assert data.get("removed") == 3, data
assert data.get("pressure") == "yes", data
' <<< "$output"
}

@test "maintenance status when not running still prints the last cleanup" {
  _record
  [ "$status" -eq 0 ]
  _status
  [ "$status" -eq 0 ]
  [[ "$output" == *"Not running"* ]]
  [[ "$output" == *"Home filesystem: 91% used (prune pressure at 85%)"* ]]
  [[ "$output" == *"Last cleanup:"* ]]
  [[ "$output" == *"$SUMMARY"* ]]
}

@test "maintenance_cleanup_record with an empty sweep line keeps repos and removed numeric" {
  SWEEP_LINE=""
  _record
  [ "$status" -eq 0 ]
  run env HOME="$TEST_HOME" WORKBENCH_STATE_DIR="$WORKBENCH_STATE_DIR" \
    "$REPO_ROOT/ai/bin/otto-log" query --script maintenance --json
  [ "$status" -eq 0 ]
  python3 -c '
import json, sys
events = [json.loads(line) for line in sys.stdin if line.strip()]
hits = [e for e in events if e.get("action") == "cleanup"]
assert hits, events
data = hits[0].get("data") or {}
for k in ("repos", "removed"):
    assert str(data.get(k, 0)).isdigit(), data
' <<< "$output"
}

@test "maintenance status with no cleanup file prints disk use and no last-cleanup line" {
  _status
  [ "$status" -eq 0 ]
  [[ "$output" == *"Home filesystem: 91% used (prune pressure at 85%)"* ]]
  [[ "$output" != *"Last cleanup:"* ]]
}

@test "the maintenance script records cleanup before it stamps MAINTENANCE_LAST_FILE" {
  # Two separate properties, and grepping for each independently would pass on
  # a file that had them in either order — so compare line numbers. The record
  # has to come first: a stamp written before the record would report a cycle
  # that never persisted its summary.
  local script="$REPO_ROOT/maintenance/bin/otto-workbench-maintenance"
  local call_line stamp_line
  call_line="$(grep -n '^[^#]*maintenance_cleanup_record' "$script" | head -1 | cut -d: -f1)"
  stamp_line="$(grep -n '^[^#]*MAINTENANCE_LAST_FILE' "$script" | tail -1 | cut -d: -f1)"
  [ -n "$call_line" ]
  [ -n "$stamp_line" ]
  [ "$call_line" -lt "$stamp_line" ]
}
