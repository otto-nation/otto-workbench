#!/usr/bin/env bats
# Tests for maintenance/bin/prune-caches — Go build cache, mise installs, Docker images.
#
# select-on: maintenance/bin/prune-caches lib/constants.sh lib/portable.sh ai/lib/config/workbench_config.py

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  load 'maintenance_prune_helper'
  common_setup
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME"
  export HOME="$TEST_HOME"
  gate_sandbox

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

@test "no go or mise on PATH reports go=absent mise=off and calls nothing" {
  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"go=absent"* ]]
  [[ "$output" == *"mise=off"* ]]
  [ ! -f "$CALLS" ]
}

@test "a Go cache under the default threshold is below and not cleaned" {
  _stub_go
  mkdir -p "$TEST_HOME/gocache"
  printf '%4096s' x > "$TEST_HOME/gocache/x"

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"go=below"* ]]
  [ ! -f "$CALLS" ]
  [ -f "$TEST_HOME/gocache/x" ]
}

@test "go_cache_gb 0 cleans the cache and reports a GB figure" {
  _stub_go
  mkdir -p "$TEST_HOME/gocache"
  printf '%4096s' x > "$TEST_HOME/gocache/x"
  _write_config $'maintenance:\n  prune:\n    go_cache_gb: 0\n'

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"go="*[0-9]*"."*[0-9]"GB"* ]]
  [ -f "$CALLS" ]
  [ "$(cat "$CALLS")" = "clean -cache" ]
  [ ! -f "$TEST_HOME/gocache/x" ]
}

@test "defaults with a mise stub present leave mise=off and do not call it" {
  _stub_mise
  mkdir -p "$TEST_HOME/mise/installs/old"

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"mise=off"* ]]
  [ ! -f "$CALLS" ]
  [ -d "$TEST_HOME/mise/installs/old" ]
}

@test "go_cache false reports go=off and does not call go" {
  _stub_go
  mkdir -p "$TEST_HOME/gocache"
  printf '%4096s' x > "$TEST_HOME/gocache/x"
  _write_config $'maintenance:\n  prune:\n    go_cache: false\n'

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"go=off"* ]]
  [ ! -f "$CALLS" ]
  [ -f "$TEST_HOME/gocache/x" ]
}

@test "a go prune records a prune-go event with numeric bytes_freed" {
  _stub_go
  mkdir -p "$TEST_HOME/gocache"
  printf '%4096s' x > "$TEST_HOME/gocache/x"
  _write_config $'maintenance:\n  prune:\n    go_cache_gb: 0\n'

  _run_prune
  [ "$status" -eq 0 ]

  run "$REPO_ROOT/ai/bin/otto-log" query --script maintenance --json
  [ "$status" -eq 0 ]
  [[ "$output" == *"prune-go"* ]]
  [[ "$output" == *"bytes_freed"* ]]
  python3 -c '
import json, sys
events = [json.loads(line) for line in sys.stdin if line.strip()]
hits = [e for e in events if e.get("action") == "prune-go"]
assert hits, events
freed = hits[0].get("data", {}).get("bytes_freed")
assert isinstance(freed, (int, float)), freed
' <<< "$output"
}

@test "docker and mise enabled with both tools absent report mise=absent docker=absent" {
  _write_config $'maintenance:\n  prune:\n    docker: true\n    mise: true\n'

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"mise=absent"* ]]
  [[ "$output" == *"docker=absent"* ]]
}

@test "defaults with a docker stub present leave docker=off and do not call it" {
  _stub_docker

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"docker=off"* ]]
  [ ! -f "$CALLS" ]
}

@test "docker true on an owned unix socket prunes images without --volumes" {
  _stub_docker
  python3 -c 'import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$TEST_HOME/docker.sock"
  _write_config $'maintenance:\n  prune:\n    docker: true\n'

  _run_prune
  [ "$status" -eq 0 ]
  grep -qx -- 'image prune -a -f --filter until=168h' "$CALLS"
  ! grep -q -- '--volumes' "$CALLS"
  [[ "$output" == *"docker=1.4GB"* ]]
}

@test "docker true with DOCKER_HOST pointing at a regular file is not-own" {
  _stub_docker
  printf x > "$TEST_HOME/not-a-socket"
  export DOCKER_HOST="unix://$TEST_HOME/not-a-socket"
  _write_config $'maintenance:\n  prune:\n    docker: true\n'

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"docker=not-own"* ]]
  [ ! -f "$CALLS" ]
}

@test "docker reclaimed space in MB and with a spaced unit is parsed" {
  _stub_docker
  python3 -c 'import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$TEST_HOME/docker.sock"
  _write_config $'maintenance:\n  prune:\n    docker: true\n'
  STUB_DOCKER_RECLAIMED="1500 MB"
  export STUB_DOCKER_RECLAIMED

  _run_prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"docker=1.4GB"* ]]
}
