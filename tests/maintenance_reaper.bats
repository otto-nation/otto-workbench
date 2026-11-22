#!/usr/bin/env bats
# Tests for maintenance/bin/reap-hung — terminating processes past their limit.
#
# select-on: maintenance/bin/reap-hung lib/heavy_commands.sh lib/constants.sh ai/lib/config/workbench_config.py

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME/stub-bin"
  export HOME="$TEST_HOME"
  gate_sandbox
  STUB_BIN="$TEST_HOME/stub-bin"
  PS_TABLE="$TEST_HOME/ps-table"
  : > "$PS_TABLE"
  SPAWNED=()
  # The listing is a stub: etimes is Linux-only and a test cannot wait 30
  # minutes. The processes the script signals are real.
  cat > "$STUB_BIN/ps" <<EOF2
#!/usr/bin/env bash
# Identity queries (-o comm= / -o etimes= -p PID) answer from the table, or from
# the override file for comm; other -p queries are the real ps. The args listing
# is matched before the -p passthrough so -o pid=,args= is never swallowed.
case "\$*" in
  "-o comm= -p "*)
    if [[ -f "$PS_TABLE.comm" ]]; then cat "$PS_TABLE.comm"; exit 0; fi
    awk -v p="\${@: -1}" '\$1 == p { print \$4 }' "$PS_TABLE" ;;
  "-o etimes= -p "*)
    awk -v p="\${@: -1}" '\$1 == p { print \$3 }' "$PS_TABLE" ;;
  *"-o pid=,args="*)
    awk '{
      if (NF >= 5) {
        printf "%s", \$1
        for (i = 5; i <= NF; i++) printf " %s", \$i
        printf "\\n"
      } else {
        print \$1, \$4
      }
    }' "$PS_TABLE" ;;
  *" -p "*)
    PATH="\${PATH#"$STUB_BIN:"}" exec ps "\$@" ;;
  *) awk '{print \$1,\$2,\$3,\$4}' "$PS_TABLE" ;;
esac
EOF2
  chmod +x "$STUB_BIN/ps"
}

teardown() {
  local pid
  for pid in "${SPAWNED[@]}"; do
    kill -KILL "$pid" 2>/dev/null || true
  done
  common_teardown
}

# _spawn NAME — a real process, orphaned so init reaps it once it dies. The pid
# lands in SPAWNED and in the variable NAME.
_spawn() {
  local -n __pid=$1
  __pid="$( (sleep 300 > /dev/null 2>&1 3>&- & echo $!) )"
  SPAWNED+=("$__pid")
}

# _spawn_deaf NAME — as _spawn, but ignoring SIGTERM.
_spawn_deaf() {
  local -n __pid=$1
  __pid="$( (trap '' TERM; sleep 300 > /dev/null 2>&1 3>&- & echo $!) )"
  SPAWNED+=("$__pid")
}

# _row PID PPID AGE_S COMM [ARGS...]
_row() {
  {
    printf '%s %s %s %s' "$1" "$2" "$3" "$4"
    if (($# > 4)); then
      shift 4
      printf ' %s' "$@"
    fi
    printf '\n'
  } >> "$PS_TABLE"
}

_alive() {
  kill -0 "$1" 2>/dev/null
}

_config() {
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf '%s\n' "$1" > "$WORKBENCH_CONFIG_DIR/config.yml"
}

_run_reap() {
  run --separate-stderr env \
    PATH="$STUB_BIN:$PATH" \
    HOME="$TEST_HOME" \
    WORKBENCH_STATE_DIR="$WORKBENCH_STATE_DIR" \
    WORKBENCH_CACHE_DIR="$WORKBENCH_CACHE_DIR" \
    WORKBENCH_CONFIG_DIR="$WORKBENCH_CONFIG_DIR" \
    bash -c "cd / && '$REPO_ROOT/maintenance/bin/reap-hung'"
}

@test "a golangci-lint past its limit is terminated and counted" {
  local pid
  _spawn pid
  _row "$pid" 99999 $(( 31 * 60 )) golangci-lint
  _run_reap
  [ "$status" -eq 0 ]
  [ "$output" = "reaped=1" ]
  run ! _alive "$pid"
}

@test "a process under its limit is left alone and nothing is printed" {
  local pid
  _spawn pid
  _row "$pid" 99999 $(( 29 * 60 )) golangci-lint
  _run_reap
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  _alive "$pid"
}

@test "a name that merely contains a rule's command is not matched" {
  local pid
  _spawn pid
  _row "$pid" 99999 $(( 600 * 60 )) golangci-lint-wrapper
  _run_reap
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  _alive "$pid"
}

@test "reaper.enabled false reaps nothing" {
  local pid
  _spawn pid
  _row "$pid" 99999 $(( 600 * 60 )) golangci-lint
  _config $'reaper:\n  enabled: false\n'
  _run_reap
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  _alive "$pid"
}

@test "a limit set in config applies to that command only" {
  local gh_pid lint_pid
  _spawn gh_pid
  _spawn lint_pid
  _row "$gh_pid" 99999 $(( 2 * 60 )) gh
  _row "$lint_pid" 99999 $(( 2 * 60 )) golangci-lint
  _config $'reaper:\n  max_minutes:\n    gh: 1\n'
  _run_reap
  [ "$output" = "reaped=1" ]
  run ! _alive "$gh_pid"
  _alive "$lint_pid"
}

@test "an over-limit parent takes its children, but never a git child" {
  local parent child git_child
  _spawn parent
  _spawn child
  _spawn git_child
  _row "$parent" 99999 $(( 46 * 60 )) tasks
  _row "$child" "$parent" $(( 40 * 60 )) golangci-lint
  _row "$git_child" "$parent" $(( 40 * 60 )) git
  _run_reap
  [ "$output" = "reaped=2" ]
  run ! _alive "$parent"
  run ! _alive "$child"
  _alive "$git_child"
}

@test "an over-limit child under an in-limit parent is reaped alone" {
  local parent child
  _spawn parent
  _spawn child
  _row "$parent" 99999 $(( 10 * 60 )) tasks
  _row "$child" "$parent" $(( 31 * 60 )) golangci-lint
  _run_reap
  [ "$output" = "reaped=1" ]
  _alive "$parent"
  run ! _alive "$child"
}

@test "an over-limit lint child of a pre-push hook is not reaped" {
  local hook child
  _spawn hook
  _spawn child
  _row "$hook" 99999 $(( 10 * 60 )) bash /usr/local/share/git/hooks/pre-push
  _row "$child" "$hook" $(( 31 * 60 )) golangci-lint
  _run_reap
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  _alive "$hook"
  _alive "$child"
}

@test "an over-limit tasks process that is the pre-commit hook is not reaped" {
  local pid
  _spawn pid
  _row "$pid" 99999 $(( 46 * 60 )) python3 python3 -m tasks lint /repo/.git/hooks/pre-commit
  _run_reap
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  _alive "$pid"
}

@test "a process that ignores SIGTERM is killed after the grace period" {
  local pid
  _spawn_deaf pid
  _row "$pid" 99999 $(( 31 * 60 )) golangci-lint
  _config $'reaper:\n  grace_s: 5\n'
  _run_reap
  [ "$output" = "reaped=1" ]
  [[ "$stderr" == *"SIGKILL $pid"* ]]
  run ! _alive "$pid"
}

@test "a pid that is no longer the listed process is not sent SIGKILL" {
  local pid
  _spawn_deaf pid
  _row "$pid" 99999 $(( 31 * 60 )) golangci-lint
  # After the TERM the pid answers as an unrelated command, as a reused one would.
  printf 'unrelated\n' > "$PS_TABLE.comm"
  _config $'reaper:\n  grace_s: 5\n'
  _run_reap
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"no longer golangci-lint"* ]]
  _alive "$pid"
}

@test "a python -m tasks runner past its limit takes its children, but never a git child" {
  local parent child git_child
  _spawn parent
  _spawn child
  _spawn git_child
  _row "$parent" 99999 $(( 46 * 60 )) python3 /opt/py/bin/python3 -m tasks lint go --repo r
  _row "$child" "$parent" $(( 20 * 60 )) golangci-lint
  _row "$git_child" "$parent" $(( 20 * 60 )) git
  _run_reap
  [ "$status" -eq 0 ]
  [ "$output" = "reaped=2" ]
  run ! _alive "$parent"
  run ! _alive "$child"
  _alive "$git_child"
}

@test "a python -m tasks runner under its limit is left alone" {
  local pid
  _spawn pid
  _row "$pid" 99999 $(( 40 * 60 )) python3 /opt/py/bin/python3 -m tasks lint go --repo r
  _run_reap
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  _alive "$pid"
}

@test "python invocations that are not -m tasks are not matched" {
  local other script cflag
  _spawn other
  _spawn script
  _spawn cflag
  _row "$other" 99999 $(( 600 * 60 )) python3 python3 -m tasks_other
  _row "$script" 99999 $(( 600 * 60 )) python3 python3 /x/tasks/y.py
  _row "$cflag" 99999 $(( 600 * 60 )) python3 python3 -c tasks
  _run_reap
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  _alive "$other"
  _alive "$script"
  _alive "$cflag"
}

@test "a python3.12 -m tasks runner past its limit is reaped" {
  local pid
  _spawn pid
  _row "$pid" 99999 $(( 46 * 60 )) python3.12 python3.12 -m tasks
  _run_reap
  [ "$status" -eq 0 ]
  [ "$output" = "reaped=1" ]
  run ! _alive "$pid"
}

@test "a python runner with leading options before -m tasks is reaped, a script's -m tasks is not" {
  local opts script substr
  _spawn opts
  _spawn script
  _spawn substr
  _row "$opts" 99999 $(( 46 * 60 )) python3 python3 -u -X dev -m tasks lint go
  _row "$script" 99999 $(( 600 * 60 )) python3 python3 x.py -m tasks
  _row "$substr" 99999 $(( 600 * 60 )) python3 python3 -u -m tasksx
  _run_reap
  [ "$status" -eq 0 ]
  [ "$output" = "reaped=1" ]
  run ! _alive "$opts"
  _alive "$script"
  _alive "$substr"
}

@test "a deaf python -m tasks runner is killed after the grace period" {
  local pid
  _spawn_deaf pid
  _row "$pid" 99999 $(( 46 * 60 )) python3 python3 -m tasks
  _config $'reaper:\n  grace_s: 5\n'
  _run_reap
  [ "$status" -eq 0 ]
  [ "$output" = "reaped=1" ]
  [[ "$stderr" == *"SIGKILL $pid"* ]]
  run ! _alive "$pid"
}

@test "a pid that is no longer the listed python -m tasks runner is not sent SIGKILL" {
  local pid
  _spawn_deaf pid
  _row "$pid" 99999 $(( 46 * 60 )) python3 python3 -m tasks
  printf 'unrelated\n' > "$PS_TABLE.comm"
  _config $'reaper:\n  grace_s: 5\n'
  _run_reap
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"no longer tasks"* ]]
  _alive "$pid"
}

@test "the script's own ancestors are never signalled" {
  _row "$$" 99999 $(( 600 * 60 )) go
  _row "$BASHPID" 99999 $(( 600 * 60 )) go
  _run_reap
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a failing ps is a warning and exit 0" {
  printf '#!/usr/bin/env bash\nexit 1\n' > "$STUB_BIN/ps"
  _run_reap
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"nothing reaped"* ]]
}
