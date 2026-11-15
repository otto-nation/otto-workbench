#!/usr/bin/env bats
# Tests for maintenance/bin/prune-caches — Go build cache, mise installs, Docker images.
#
# select-on: maintenance/bin/prune-caches lib/constants.sh ai/lib/config/workbench_config.py

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
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

# _build_narrow — symlink-only PATH, no /usr/bin or /bin appended.
_build_narrow() {
  local tool real
  ln -sf "$BASH" "$NARROW/bash"
  for tool in python3 yq git awk du dirname date cat mkdir mv rm sed tr mktemp head sort; do
    real="$(_runnable_binary "$tool" || true)"
    if [[ -z "$real" ]]; then
      echo "narrow: no runnable $tool on PATH; leaving it out" >&2
      continue
    fi
    ln -sf "$real" "$NARROW/$tool"
  done
}

_stub_df() {
  cat > "$STUB_BIN/df" <<EOF
#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
printf '/dev/disk 100 50 50 %s%% /\n' "\${STUB_DF_PCT:-50}"
EOF
  chmod +x "$STUB_BIN/df"
}

_stub_go() {
  cat > "$STUB_BIN/go" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == env && "\$2" == GOCACHE ]]; then
  printf '%s\n' "$TEST_HOME/gocache"
  exit 0
fi
if [[ "\$1" == clean && "\$2" == -cache ]]; then
  printf '%s\n' "\$*" >> "$CALLS"
  rm -rf "$TEST_HOME/gocache"
  mkdir -p "$TEST_HOME/gocache"
  exit 0
fi
exit 1
EOF
  chmod +x "$STUB_BIN/go"
}

_stub_mise() {
  cat > "$STUB_BIN/mise" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$CALLS"
rm -rf "$TEST_HOME/mise/installs/old"
exit 0
EOF
  chmod +x "$STUB_BIN/mise"
}

_stub_docker() {
  cat > "$STUB_BIN/docker" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$CALLS"
if [[ "\$1" == context && "\$2" == inspect ]]; then
  printf 'unix://%s\n' "$TEST_HOME/docker.sock"
  exit 0
fi
if [[ "\$1" == image ]]; then
  printf 'Total reclaimed space: 1.5GB\n'
  exit 0
fi
exit 1
EOF
  chmod +x "$STUB_BIN/docker"
}

_write_config() {
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf '%s\n' "$1" > "$WORKBENCH_CONFIG_DIR/config.yml"
}

# _run_prune — from `/`, the cwd launchd gives the timer. env -i so the
# ambient PATH cannot smuggle in the machine's real go/mise.
_run_prune() {
  run --separate-stderr env -i \
    HOME="$TEST_HOME" \
    PATH="$STUB_BIN:$NARROW" \
    TMPDIR="$TMPDIR" \
    WORKBENCH_STATE_DIR="$WORKBENCH_STATE_DIR" \
    WORKBENCH_CACHE_DIR="$WORKBENCH_CACHE_DIR" \
    WORKBENCH_CONFIG_DIR="$WORKBENCH_CONFIG_DIR" \
    MISE_DATA_DIR="$TEST_HOME/mise" \
    CALLS="$CALLS" \
    STUB_DF_PCT="${STUB_DF_PCT:-50}" \
    DOCKER_HOST="${DOCKER_HOST:-}" \
    bash -c "cd / && '$SCRIPT'"
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

@test "docker ownership check uses -O on the socket" {
  grep -q -- '-O "$sock"' "$SCRIPT"
}
