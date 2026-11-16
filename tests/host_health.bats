#!/usr/bin/env bats
# Tests for bin/host-health — load, memory, processes, heavy commands, queue.
#
# select-on: bin/host-health

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  STUB_BIN="$BATS_TEST_TMPDIR/stub-bin"
  PROC="$BATS_TEST_TMPDIR/proc"
  mkdir -p "$STUB_BIN" "$PROC"
  HH="$REPO_ROOT/bin/host-health"
}

teardown() {
  common_teardown
}

# _bin_dirs — directories of the tools host-health needs, without ~/.local/bin.
_bin_dirs() {
  local name resolved dir path=""
  for name in bash env jq awk sed date hostname df uname sort tail nproc sysctl ps readlink; do
    resolved="$(command -v "$name" 2>/dev/null)" || continue
    dir="$(dirname "$resolved")"
    case ":$path:" in
      *":$dir:"*) continue ;;
    esac
    if [[ -n "$path" ]]; then
      path="$path:$dir"
    else
      path="$dir"
    fi
  done
  printf '%s\n' "$path"
}

# _hh_path — STUB_BIN first, then the tool dirs. No otto-queue unless stubbed.
_hh_path() {
  printf '%s:%s\n' "$STUB_BIN" "$(_bin_dirs)"
}

# _write_proc — fake /proc/loadavg and meminfo for the figures the tests assert.
_write_proc() {
  printf '%s\n' '1.50 1.00 0.50 1/100 1' > "$PROC/loadavg"
  cat > "$PROC/meminfo" <<'EOF'
MemTotal:       8192000 kB
MemAvailable:   4096000 kB
SwapTotal:      2048000 kB
SwapFree:       1024000 kB
EOF
}

# _stub_ps BODY — executable ps stub whose body is BODY.
_stub_ps() {
  printf '#!/usr/bin/env bash\n%s\n' "$1" > "$STUB_BIN/ps"
  chmod +x "$STUB_BIN/ps"
}

# _stub_queue BODY — executable otto-queue stub whose body is BODY.
_stub_queue() {
  printf '#!/usr/bin/env bash\n%s\n' "$1" > "$STUB_BIN/otto-queue"
  chmod +x "$STUB_BIN/otto-queue"
}

# _run_hh [args...] — host-health with the fake proc, Linux mem, and stub PATH.
_run_hh() {
  run env PATH="$(_hh_path)" \
    HOST_HEALTH_PROC="$PROC" \
    HOST_HEALTH_OS=Linux \
    "$HH" "$@"
}

@test "--json reports load and mem from a fake /proc" {
  _write_proc
  _run_hh --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.load.m1==1.5 and .mem.total_mb==8000 and .mem.available_mb==4000 and .mem.swap_used_mb==1000' \
    <<< "$json"
  [ "$status" -eq 0 ]
}

@test "ps listing fills procs, zombies, heavy, top_cpu, top_mem and etime" {
  _stub_ps 'cat <<'\''PS'\''
10 alice 01:02:03 90.0 1.0 R /usr/bin/golangci-lint
11 bob 2-00:00:00 5.0 40.0 S go
12 bob 00:05 0.0 0.0 Z defunct
PS'
  _run_hh --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.procs.count==3 and .procs.zombies==1 and .heavy["golangci-lint"]==1 and .heavy.go==1 and .procs.top_cpu[0].command=="golangci-lint" and .procs.top_mem[0].command=="go" and .procs.top_cpu[0].age_s==3723 and .procs.top_mem[0].age_s==172800' \
    <<< "$json"
  [ "$status" -eq 0 ]
}

@test "missing meminfo yields exit 0 and .mem null" {
  _run_hh --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.mem == null' <<< "$json"
  [ "$status" -eq 0 ]
}

@test "a failing ps yields exit 0 and null procs and heavy" {
  _stub_ps 'exit 1'
  _run_hh --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.procs == null and .heavy == null' <<< "$json"
  [ "$status" -eq 0 ]
}

@test "otto-queue absent from PATH yields .queue null" {
  local path
  path="$(_hh_path)"
  run env PATH="$path" command -v otto-queue
  [ "$status" -ne 0 ]
  _run_hh --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.queue == null' <<< "$json"
  [ "$status" -eq 0 ]
}

@test "otto-queue exit 75 yields .queue.busy true" {
  _stub_queue 'exit 75'
  _run_hh --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.queue.busy == true' <<< "$json"
  [ "$status" -eq 0 ]
}

@test "otto-queue JSON lines count running and queued" {
  _stub_queue 'printf "%s\n" "{\"state\":\"running\"}" "{\"state\":\"queued\"}"'
  _run_hh --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.queue.running==1 and .queue.queued==1' <<< "$json"
  [ "$status" -eq 0 ]
}

@test "unknown flag exits 2 and names the argument" {
  run "$HH" --nope
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown argument"* ]]
}

@test "-h prints usage and exits 0" {
  run "$HH" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"host-health"* ]]
  [[ "$output" == *"--json"* ]]
}

@test "text mode exits 0 and mentions load and cores" {
  _write_proc
  _run_hh
  [ "$status" -eq 0 ]
  [[ "$output" == *"load"* ]]
  [[ "$output" == *"cores"* ]]
}

@test "JSON without --reaper has no reaper key" {
  _write_proc
  _run_hh --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e 'has("reaper")|not' <<< "$json"
  [ "$status" -eq 0 ]
}
