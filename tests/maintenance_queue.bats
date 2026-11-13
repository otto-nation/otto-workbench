#!/usr/bin/env bats
# Tests for the queue entry of maintenance/bin/otto-workbench-maintenance: the
# timer only submits, the queue runs the body, and a live `pr batch` run makes
# the body defer with 75 so the queue retries it rather than syncing code under
# the batch.
bats_require_minimum_version 1.5.0
# select-on: maintenance/bin/otto-workbench-maintenance bin/otto-workbench lib/maintenance.sh

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME/.local/bin" "$TEST_HOME/Library/Logs/otto-workbench"
  export HOME="$TEST_HOME"
  QUEUE_CALLS="$TEST_HOME/queue-calls"
  printf '#!/usr/bin/env bash\necho "$*" >> "%s"\nexit 0\n' "$QUEUE_CALLS" > "$TEST_HOME/otto-queue"
  chmod +x "$TEST_HOME/otto-queue"
  # A git that fails at once: a run that gets past the queue entry stops at its
  # first fetch, before it can touch anything.
  printf '#!/usr/bin/env bash\nexit 1\n' > "$TEST_HOME/.local/bin/git"
  chmod +x "$TEST_HOME/.local/bin/git"
  SCRIPT="$REPO_ROOT/maintenance/bin/otto-workbench-maintenance"
}

teardown() {
  common_teardown
}

# _batch ACTIVE — a `pr` whose `batch status --list` reports one run, active or not.
_batch() {
  printf '#!/usr/bin/env bash\necho %q\n' \
    "{\"schema_version\": 2, \"runs\": [{\"id\": \"r1\", \"active\": $1}]}" > "$TEST_HOME/.local/bin/pr"
  chmod +x "$TEST_HOME/.local/bin/pr"
}

# _maint [ENV...] -- [ARGS...] — the script under the timer's bare environment.
_maint() {
  local -a extra=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do extra+=("$1"); shift; done
  [[ "${1:-}" == "--" ]] && shift
  run env -i HOME="$TEST_HOME" PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    WORKBENCH_STATE_DIR="$WORKBENCH_STATE_DIR" WORKBENCH_CACHE_DIR="$WORKBENCH_CACHE_DIR" \
    WORKBENCH_CONFIG_DIR="$WORKBENCH_CONFIG_DIR" OTTO_QUEUE_BIN="$TEST_HOME/otto-queue" \
    "${extra[@]}" "$SCRIPT" "$@"
}

@test "a scheduled run submits itself to the queue and does nothing else" {
  _maint
  [ "$status" -eq 0 ]
  [ "$(cat "$QUEUE_CALLS")" = "submit --class heavy --key maintenance --no-reuse -- $SCRIPT" ]
  [[ "$output" != *"Fetching"* ]]
}

@test "maintenance run --ticket runs it as a ticket under the same key" {
  _maint -- --ticket
  [ "$status" -eq 0 ]
  [ "$(cat "$QUEUE_CALLS")" = "run --ticket --class heavy --key maintenance --no-reuse -- $SCRIPT" ]
}

@test "a run the queue started defers with 75 while a batch is live" {
  _batch true
  _maint OTTO_QUEUE_ENTRY=7
  [ "$status" -eq 75 ]
  [[ "$output" == *"deferring"* ]]
  [[ "$output" != *"Fetching"* ]]
  [ ! -e "$QUEUE_CALLS" ]
}

@test "a run the queue ran unqueued does not submit itself again" {
  _batch true
  _maint _WB_MAINTENANCE_QUEUED=1
  [ "$status" -eq 75 ]
  [ ! -e "$QUEUE_CALLS" ]
}

# passes-at-base: the preflight must not stop a run with no live batch
@test "with no live batch the queued run goes on to fetch" {
  _batch false
  _maint OTTO_QUEUE_ENTRY=7
  [ "$status" -eq 1 ]
  [[ "$output" == *"Fetching origin/main"* ]]
}

# passes-at-base: the preflight must not stop a run with no live batch
@test "with no pr installed the batch check is skipped" {
  _maint OTTO_QUEUE_ENTRY=7
  [ "$status" -eq 1 ]
  [[ "$output" == *"Fetching origin/main"* ]]
}

@test "otto-workbench maintenance run passes --ticket" {
  grep -q '"\$MAINTENANCE_SRC_DIR/bin/otto-workbench-maintenance" --ticket$' "$REPO_ROOT/bin/otto-workbench"
}

@test "a missing otto-queue runs the body without a queue call" {
  _maint OTTO_QUEUE_BIN="$TEST_HOME/no-such-queue"
  [ "$status" -eq 1 ]
  [[ "$output" == *"Fetching origin/main"* ]]
  [ ! -e "$QUEUE_CALLS" ]
}

# _logs — otto-workbench maintenance logs under a stub HOME.
_logs() {
  mkdir -p "$TEST_HOME/.local/bin" "$TEST_HOME/Library/Logs/otto-workbench"
  printf '#!/usr/bin/env bash\necho JOURNAL\n' > "$TEST_HOME/.local/bin/journalctl"
  chmod +x "$TEST_HOME/.local/bin/journalctl"
  run env HOME="$TEST_HOME" PATH="$TEST_HOME/.local/bin:$PATH" NO_COLOR=1 \
    WORKBENCH_STATE_DIR="$WORKBENCH_STATE_DIR" WORKBENCH_CACHE_DIR="$WORKBENCH_CACHE_DIR" \
    WORKBENCH_CONFIG_DIR="$WORKBENCH_CONFIG_DIR" OTTO_QUEUE_BIN="$TEST_HOME/otto-queue" \
    "$REPO_ROOT/bin/otto-workbench" --workbench-dir "$REPO_ROOT" maintenance logs
}

@test "maintenance logs tails the newest queued maintenance run" {
  echo old-run > "$TEST_HOME/old.log"
  echo new-run > "$TEST_HOME/new.log"
  echo other-run > "$TEST_HOME/other.log"
  cat > "$TEST_HOME/otto-queue" <<EOF
#!/usr/bin/env bash
cat <<'JSON'
{"id": 4, "idem_key": "maintenance", "log_path": "$TEST_HOME/old.log"}
{"id": 1, "idem_key": "other", "log_path": "$TEST_HOME/other.log"}
{"id": 8, "idem_key": "maintenance", "log_path": "$TEST_HOME/new.log"}
JSON
EOF
  chmod +x "$TEST_HOME/otto-queue"
  echo legacy-run > "$TEST_HOME/Library/Logs/otto-workbench/maintenance.log"
  _logs
  [ "$status" -eq 0 ]
  [[ "$output" == *"new-run"* ]]
  [[ "$output" != *"old-run"* ]]
  [[ "$output" != *"legacy-run"* ]]
  [[ "$output" != *"other-run"* ]]
}

@test "maintenance logs falls back when otto-queue errors" {
  printf '#!/usr/bin/env bash\nexit 1\n' > "$TEST_HOME/otto-queue"
  chmod +x "$TEST_HOME/otto-queue"
  echo legacy-run > "$TEST_HOME/Library/Logs/otto-workbench/maintenance.log"
  _logs
  [ "$status" -eq 0 ]
  if [[ "$OSTYPE" == darwin* ]]; then
    [[ "$output" == *"legacy-run"* ]]
  else
    [[ "$output" == *"JOURNAL"* ]]
  fi
}
