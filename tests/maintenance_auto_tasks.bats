#!/usr/bin/env bats
# Tests for the auto-task block in maintenance/bin/otto-workbench-maintenance —
# the only thing that fires the four AI cooldown gates on a machine that does
# not live in Claude Code.
#
# Nearly every failure mode here is silent. A reaped agent, a `wiki` that is not
# on the timer's PATH, a gate whose exit 1 kills the run before the stamp is
# written — each one logs a plausible line and exits 0, so the cases below
# assert on what actually got spawned rather than on the script's status.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME"
  export HOME="$TEST_HOME"
  gate_sandbox

  RUNNER="$REPO_ROOT/maintenance/bin/run-due-auto-tasks"
  SPAWN_LOG="$TEST_HOME/spawned"
  STUB_BIN="$TEST_HOME/stub-bin"
  mkdir -p "$STUB_BIN"

  # The executor. The block skips whole without both of these.
  _stub_claude
  _stub_run_auto_task
}

teardown() {
  common_teardown
}

# _stub_claude — the executor presence check. The block skips whole without it.
_stub_claude() {
  printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB_BIN/claude"
  chmod +x "$STUB_BIN/claude"
}

# _stub_run_auto_task — records each spawn as "<skill> <cwd>", one per line.
# This is the assertion surface for every case: what ran, and where from.
_stub_run_auto_task() {
  mkdir -p "$TEST_HOME/.local/bin"
  printf '#!/usr/bin/env bash\nprintf "%%s %%s\\n" "$1" "$PWD" >> "%s"\nexit 0\n' \
    "$SPAWN_LOG" > "$TEST_HOME/.local/bin/run-auto-task"
  chmod +x "$TEST_HOME/.local/bin/run-auto-task"
}

# _stub_gate SKILL EXIT — replace a gate with one that answers EXIT. The real
# gates are left in place otherwise, so an unstubbed one answers honestly
# against the sandboxed registry (which is empty, so: not due).
_stub_gate() {
  local skill="$1" exit_code="$2"
  local dir="$TEST_HOME/skills/$skill"
  mkdir -p "$dir"
  printf '#!/usr/bin/env bash\nexit %s\n' "$exit_code" > "$dir/should-$skill.sh"
  chmod +x "$dir/should-$skill.sh"
}

# _run_tasks — the runner, from `/` on purpose: that is the cwd launchd gives
# it, and several of the bugs this suite covers only appear there. `env -i` so
# the ambient PATH cannot smuggle in the machine's real `claude` or `wiki`.
_run_tasks() {
  run env -i \
    HOME="$TEST_HOME" \
    PATH="$STUB_BIN:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    WORKBENCH_STATE_DIR="$WORKBENCH_STATE_DIR" \
    WORKBENCH_CACHE_DIR="$WORKBENCH_CACHE_DIR" \
    WORKBENCH_CONFIG_DIR="$WORKBENCH_CONFIG_DIR" \
    AUTO_TASK_SKILLS_DIR="$TEST_HOME/skills" \
    AUTO_TASK_BIN="$TEST_HOME/.local/bin/run-auto-task" \
    bash -c "cd / && '$RUNNER'"
}

# ── The spawn cap ────────────────────────────────────────────────────────────

@test "one gate due spawns exactly that task" {
  _stub_gate dream 0
  _stub_gate promote 1
  _stub_gate retro 1
  _stub_gate wiki-capture 1
  _run_tasks
  [ "$(wc -l < "$SPAWN_LOG" | tr -d ' ')" = "1" ]
  grep -q '^dream ' "$SPAWN_LOG"
}

@test "three gates due still spawn only one task" {
  # The cap. Without it a machine with several repos due fans out an Opus agent
  # per gate per repo, unattended, on a 12h timer.
  _stub_gate dream 0
  _stub_gate promote 0
  _stub_gate retro 0
  _stub_gate wiki-capture 0
  _run_tasks
  [ "$(wc -l < "$SPAWN_LOG" | tr -d ' ')" = "1" ]
}

@test "the rarest cooldown is spawned ahead of the frequent ones" {
  # promote is 168h, retro 72h, dream 24h. At one spawn per cycle, asking the
  # 24h gate first would starve the 168h one indefinitely — it comes due rarely
  # and would lose every cycle to a gate that is due most of the time.
  _stub_gate dream 0
  _stub_gate promote 0
  _stub_gate retro 0
  _stub_gate wiki-capture 0
  _run_tasks
  grep -q '^promote ' "$SPAWN_LOG"
}

@test "the next-rarest wins once the rarest is not due" {
  _stub_gate dream 0
  _stub_gate promote 1
  _stub_gate retro 0
  _stub_gate wiki-capture 0
  _run_tasks
  grep -q '^retro ' "$SPAWN_LOG"
}

@test "wiki-capture runs only when no global gate is due" {
  # The per-repo sweep is last: it is the gate most often due, so putting it
  # ahead of the three global ones would starve all of them.
  _stub_gate dream 1
  _stub_gate promote 1
  _stub_gate retro 1
  _stub_gate wiki-capture 0
  local repo
  repo="$(gate_repo "wiki-repo")"
  _run_tasks
  [ "$(wc -l < "$SPAWN_LOG" | tr -d ' ')" = "1" ]
  grep -q "^wiki-capture $repo\$" "$SPAWN_LOG"
}

@test "the wiki-capture spawn runs in the repo it was due for" {
  # The cwd contract: run-auto-task inherits it, and the headless session
  # resolves its knowledge base and settles its cooldown from there. Spawned
  # from `/` — which is what launchd gives the timer — it would capture into
  # nothing.
  _stub_gate dream 1
  _stub_gate promote 1
  _stub_gate retro 1
  _stub_gate wiki-capture 0
  local repo
  repo="$(gate_repo "wiki-repo")"
  _run_tasks
  [ "$(cut -d' ' -f2 < "$SPAWN_LOG")" = "$repo" ]
}

@test "several repos due still spawn only one capture" {
  _stub_gate dream 1
  _stub_gate promote 1
  _stub_gate retro 1
  _stub_gate wiki-capture 0
  gate_repo "repo-one" > /dev/null
  gate_repo "repo-two" > /dev/null
  gate_repo "repo-three" > /dev/null
  _run_tasks
  [ "$(wc -l < "$SPAWN_LOG" | tr -d ' ')" = "1" ]
}

# ── errexit ──────────────────────────────────────────────────────────────────

@test "no gate due exits clean" {
  # A gate exiting 1 is the ordinary answer, not a failure. Letting it reach
  # `set -e` would take the runner down — and in the maintenance script that
  # ran this inline, it killed the run before the stamp, leaving
  # `maintenance status` reporting the timer stale forever after.
  _stub_gate dream 1
  _stub_gate promote 1
  _stub_gate retro 1
  _stub_gate wiki-capture 1
  _run_tasks
  [ "$status" -eq 0 ]
  [ ! -f "$SPAWN_LOG" ]
}

@test "a spawn failure is non-fatal" {
  _stub_gate dream 0
  _stub_gate promote 1
  _stub_gate retro 1
  _stub_gate wiki-capture 1
  printf '#!/usr/bin/env bash\nexit 7\n' > "$TEST_HOME/.local/bin/run-auto-task"
  chmod +x "$TEST_HOME/.local/bin/run-auto-task"
  _run_tasks
  [ "$status" -eq 0 ]
}

@test "the maintenance script still stamps after the auto-task block" {
  # The runner is called with `|| true`, so its exit 1 on a machine with no
  # executor cannot stop the stamp. Asserted against the real script rather
  # than the runner, since the guard lives at the call site.
  grep -A2 'run-due-auto-tasks' \
    "$REPO_ROOT/maintenance/bin/otto-workbench-maintenance" | grep -q '|| true'
  grep -q "^date '+%s' > \"\$MAINTENANCE_LAST_FILE\"" \
    "$REPO_ROOT/maintenance/bin/otto-workbench-maintenance"
}

# ── The executor skip ────────────────────────────────────────────────────────

@test "no run-auto-task symlink skips the block" {
  _stub_gate dream 0
  rm -f "$TEST_HOME/.local/bin/run-auto-task"
  _run_tasks
  [ "$status" -eq 1 ]
  [ ! -f "$SPAWN_LOG" ]
}

@test "the symlink without Claude Code skips the block" {
  # ai/claude/steps.sh only syncs the symlink when `claude` is already on PATH,
  # so the symlink outlives an uninstall. The executor is what has to be there.
  _stub_gate dream 0
  rm -f "$STUB_BIN/claude"
  _run_tasks
  [ "$status" -eq 1 ]
  [ ! -f "$SPAWN_LOG" ]
}

# ── The launchd contract ─────────────────────────────────────────────────────

@test "the rendered plist abandons the process group" {
  # launchd kills every process sharing the job's group when the job exits, and
  # run-auto-task's children are in it. Without this key the block spawns,
  # logs, and the agents are reaped before they do anything — exit 0 throughout.
  # There is no runtime symptom, so this assertion is the only thing holding it.
  #
  # Rendered the way _launchd_install does rather than read raw: the template
  # carries __INTERVAL__ where an integer belongs, so PlistBuddy cannot parse it
  # unsubstituted, and the file launchd loads is the rendered one anyway.
  local rendered="$TEST_HOME/rendered.plist"
  sed -e "s|__WORKBENCH_DIR__|/tmp/wb|g" -e "s|__INTERVAL__|43200|g" \
    -e "s|__LOG_DIR__|/tmp/logs|g" \
    "$REPO_ROOT/maintenance/maintenance.plist.template" > "$rendered"

  run /usr/libexec/PlistBuddy -c "Print :AbandonProcessGroup" "$rendered"
  [ "$status" -eq 0 ]
  [ "$output" = "true" ]
}

@test "the systemd unit does not kill the control group" {
  # The same reaping, by another name: Type=oneshot with the default
  # KillMode=control-group takes the spawned agents down with it.
  grep -q '^KillMode=process$' \
    "$REPO_ROOT/maintenance/systemd/otto-workbench-maintenance.service.template"
}
