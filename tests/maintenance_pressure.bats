#!/usr/bin/env bats
# Tests for disk-pressure mode in maintenance/bin/prune-caches.
#
# select-on: maintenance/bin/prune-caches lib/portable.sh lib/constants.sh ai/lib/config/workbench_config.py

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  load 'maintenance_prune_helper'
  common_setup
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME"
  export HOME="$TEST_HOME"
  gate_sandbox

  # shellcheck disable=SC2034  # read by _run_prune in maintenance_prune_helper.bash
  SCRIPT="$REPO_ROOT/maintenance/bin/prune-caches"
  STUB_BIN="$TEST_HOME/stub-bin"
  NARROW="$TEST_HOME/narrow"
  CALLS="$TEST_HOME/calls"
  mkdir -p "$STUB_BIN" "$NARROW"
  _build_narrow
  _stub_df
}

teardown() {
  common_teardown
}

@test "disk use one below the default limit leaves pressure off" {
  _stub_go
  _stub_mise
  _stub_docker
  mkdir -p "$TEST_HOME/gocache" "$TEST_HOME/mise/installs/old"
  printf '%4096s' x > "$TEST_HOME/gocache/x"
  STUB_DF_PCT=84

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"pressure=no"* ]]
  [[ "$output" == *"disk_pct=84"* ]]
  [[ "$output" == *"go=below"* ]]
  [[ "$output" == *"mise=off"* ]]
  [[ "$output" == *"docker=off"* ]]
  [ ! -f "$CALLS" ]
  [ -f "$TEST_HOME/gocache/x" ]
}

@test "disk use at the default limit turns pressure on and runs every step" {
  _stub_go
  _stub_mise
  _stub_docker
  mkdir -p "$TEST_HOME/gocache" "$TEST_HOME/mise/installs/old"
  printf '%4096s' x > "$TEST_HOME/gocache/x"
  python3 -c 'import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$TEST_HOME/docker.sock"
  STUB_DF_PCT=85

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"pressure=yes"* ]]
  [[ "$output" == *"disk_pct=85"* ]]
  [[ "$output" == *"go="*"GB(pressure)"* ]]
  [[ "$output" == *"mise="*"GB(pressure)"* ]]
  [[ "$output" == *"docker="*"GB(pressure)"* ]]
  grep -qx -- 'clean -cache' "$CALLS"
  grep -qx -- 'prune --yes' "$CALLS"
  grep -qx -- 'image prune -a -f --filter until=168h' "$CALLS"
  [ ! -f "$TEST_HOME/gocache/x" ]
}

@test "a raised disk_pct limit leaves pressure off when use is under it" {
  _stub_go
  _stub_mise
  _stub_docker
  mkdir -p "$TEST_HOME/gocache"
  printf '%4096s' x > "$TEST_HOME/gocache/x"
  _write_config $'maintenance:\n  disk_pct: 90\n'
  STUB_DF_PCT=89

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"pressure=no"* ]]
  [[ "$output" == *"disk_pct=89"* ]]
  [[ "$output" == *"go=below"* ]]
  [[ "$output" == *"mise=off"* ]]
  [[ "$output" == *"docker=off"* ]]
  [ ! -f "$CALLS" ]
}

@test "pressure does not prune a docker socket the user does not own" {
  _stub_docker
  printf x > "$TEST_HOME/not-a-socket"
  export DOCKER_HOST="unix://$TEST_HOME/not-a-socket"
  # shellcheck disable=SC2034  # read by _run_prune in maintenance_prune_helper.bash
  STUB_DF_PCT=85

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"pressure=yes"* ]]
  [[ "$output" == *"docker=not-own(pressure)"* ]]
  [ ! -f "$CALLS" ]
}

@test "an unreadable df leaves pressure off and exits 0" {
  cat > "$STUB_BIN/df" <<EOF
#!/usr/bin/env bash
printf 'not a df table\n'
EOF
  chmod +x "$STUB_BIN/df"

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"pressure=no"* ]]
}

@test "go env failing reports go=failed and the script still exits 0" {
  printf '#!/usr/bin/env bash\nexit 1\n' > "$STUB_BIN/go"
  chmod +x "$STUB_BIN/go"
  _stub_mise
  _stub_docker

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"go=failed"* ]]
}
