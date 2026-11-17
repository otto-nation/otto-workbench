#!/usr/bin/env bats
# Tests for bin/host-health — load, memory, processes, heavy commands, queue.
#
# select-on: bin/host-health lib/heavy_commands.sh

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

# _stub_ps BODY [ARGS_BODY] — executable ps stub. BODY answers the comm listing;
# ARGS_BODY answers `ps -o pid=,args=` (empty success when omitted, so rows fall back to comm).
_stub_ps() {
  {
    printf '%s\n' '#!/usr/bin/env bash' 'if [[ "$*" == *args=* ]]; then'
    printf '%s\n' "${2:-}"
    printf '%s\n' 'exit 0' 'fi'
    printf '%s\n' "$1"
  } > "$STUB_BIN/ps"
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

@test "python -m tasks counts as tasks; python -m tasks_x does not" {
  _stub_ps 'cat <<'\''PS'\''
10 alice 00:01 1.0 1.0 R /opt/py/bin/python3
11 bob 00:01 0.5 0.5 R python3
PS' 'cat <<'\''ARGS'\''
10 /opt/py/bin/python3 -m tasks lint go
11 python3 -m tasks_x
ARGS'
  _run_hh --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.heavy.tasks==1 and ([.procs.top_cpu[], .procs.top_mem[]] | map(has("heavy_name")) | any | not)' \
    <<< "$json"
  [ "$status" -eq 0 ]
}

@test "a non-numeric ps field yields exit 0 and null procs" {
  _stub_ps 'echo "10 alice 01:02:03 1,5 1.0 R go"'
  _run_hh --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.procs == null' <<< "$json"
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

# _stub_sysctl MEMSIZE — a sysctl stub answering the Darwin keys host-health reads. An empty MEMSIZE
# makes hw.memsize fail; any other key exits 1.
_stub_sysctl() {
  cat > "$STUB_BIN/sysctl" <<STUB
#!/usr/bin/env bash
case "\$*" in
  "-n hw.memsize") [[ -n "$1" ]] && { echo "$1"; exit 0; }; exit 1 ;;
  "-n vm.swapusage") echo "total = 2048.00M  used = 1024.50M  free = 1023.50M  (encrypted)" ;;
  "-n vm.loadavg") echo "{ 1.50 1.00 0.50 }" ;;
  "-n hw.ncpu") echo 8 ;;
  *) exit 1 ;;
esac
STUB
  chmod +x "$STUB_BIN/sysctl"
}

# _run_hh_darwin [args...] — host-health as on macOS: empty /proc dir, HOST_HEALTH_OS=Darwin.
_run_hh_darwin() {
  mkdir -p "$BATS_TEST_TMPDIR/empty-proc"
  run env PATH="$(_hh_path)" \
    HOST_HEALTH_PROC="$BATS_TEST_TMPDIR/empty-proc" \
    HOST_HEALTH_OS=Darwin \
    "$HH" "$@"
}

@test "Darwin memory, load and swap come from sysctl and vm_stat" {
  _stub_sysctl 8589934592
  printf '#!/usr/bin/env bash\n%s\n' "cat <<'VM'
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               1000.
Pages active:                              500.
Pages inactive:                           2000.
Pages speculative:                         500.
VM" > "$STUB_BIN/vm_stat"
  chmod +x "$STUB_BIN/vm_stat"
  _run_hh_darwin --json
  [ "$status" -eq 0 ]
  local json="$output"
  # (1000 + 2000 + 500) pages * 16384 B = 54.69 MB, truncated by bash integer division.
  run jq -e '.mem == {total_mb: 8192, available_mb: 54, swap_used_mb: 1024} and .load == {m1: 1.5, m5: 1, m15: 0.5}' \
    <<< "$json"
  [ "$status" -eq 0 ]
}

@test "Darwin memory is null when hw.memsize is unreadable" {
  _stub_sysctl ""
  _run_hh_darwin --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.mem == null' <<< "$json"
  [ "$status" -eq 0 ]
}

@test "Darwin without a working vm_stat counts zero free pages" {
  _stub_sysctl 8589934592
  printf '#!/usr/bin/env bash\nexit 1\n' > "$STUB_BIN/vm_stat"
  chmod +x "$STUB_BIN/vm_stat"
  _run_hh_darwin --json
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.mem.total_mb == 8192 and .mem.available_mb == 0' <<< "$json"
  [ "$status" -eq 0 ]
}

# _reaper_tree BODY — a copy of host-health in a fake workbench tree whose ai/bin/otto-log runs BODY;
# host-health finds otto-log relative to its own location, so a PATH stub would not be used.
_reaper_tree() {
  local wb="$BATS_TEST_TMPDIR/wb"
  mkdir -p "$wb/bin" "$wb/ai/bin" "$wb/lib"
  cp "$HH" "$wb/bin/host-health"
  cp "$REPO_ROOT/lib/heavy_commands.sh" "$wb/lib/heavy_commands.sh"
  printf '#!/usr/bin/env bash\n%s\n' "$1" > "$wb/ai/bin/otto-log"
  chmod +x "$wb/bin/host-health" "$wb/ai/bin/otto-log"
}

_run_reaper() {
  _write_proc
  run env PATH="$(_hh_path)" \
    HOST_HEALTH_PROC="$PROC" \
    HOST_HEALTH_OS=Linux \
    "$BATS_TEST_TMPDIR/wb/bin/host-health" --json --reaper
}

@test "--reaper reports the last reap signal and the count, ignoring other actions" {
  _reaper_tree '[[ "$*" == "query --script maintenance --since 1d --json" ]] || exit 2
cat <<'\''LOG'\''
{"ts":"2026-11-16T10:00:00Z","action":"reap-TERM"}
{"ts":"2026-11-16T12:00:00Z","action":"reap-KILL"}
{"ts":"2026-11-16T13:00:00Z","action":"something-else"}
LOG'
  _run_reaper
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.reaper == {last_at: "2026-11-16T12:00:00Z", signals_24h: 2}' <<< "$json"
  [ "$status" -eq 0 ]
}

@test "--reaper with no reap actions is zero with a null last_at" {
  _reaper_tree 'echo "{\"ts\":\"2026-11-16T13:00:00Z\",\"action\":\"something-else\"}"'
  _run_reaper
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.reaper == {last_at: null, signals_24h: 0}' <<< "$json"
  [ "$status" -eq 0 ]
}

@test "--reaper is null when otto-log fails, and the report still prints" {
  _reaper_tree 'exit 1'
  _run_reaper
  [ "$status" -eq 0 ]
  local json="$output"
  run jq -e '.reaper == null and .load.m1 == 1.5' <<< "$json"
  [ "$status" -eq 0 ]
}
