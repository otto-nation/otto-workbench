#!/usr/bin/env bats
# Tests for run-auto-task — the headless spawner behind the Stop-hook gates.
#
# The bug these exist for: the script passed `claude --bare`, which prevented a
# hook cascade but also stopped `/skill` resolving. Every run from 2026-09-15 on
# logged `Unknown command: /<skill>` and exited 0, so the four auto-tasks were a
# no-op for six days and nothing noticed. The pipeline recorded a usage row and
# a log file either way — the two things that were asserted — and never that the
# agent had run. So the assertions here are on the invocation and on the
# cascade, the two properties whose absence let that through.
#
# `claude` is stubbed: what matters is the argv this script builds and the
# environment it exports, not what a real headless session would answer.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME"
  export HOME="$TEST_HOME"
  gate_sandbox

  RUN_AUTO_TASK="$REPO_ROOT/ai/claude/bin/run-auto-task"
  export CLAUDE_LOG_DIR="$TEST_HOME/logs"
  mkdir -p "$CLAUDE_LOG_DIR"

  # Stub bin ahead of the real tools. `claude` records its argv and the
  # sentinel it inherited; `ai-usage-log` is absent unless a test adds it, so
  # the default path through the script is the nohup fallback.
  STUB_BIN="$TEST_HOME/stub-bin"
  mkdir -p "$STUB_BIN"
  ARGV_LOG="$TEST_HOME/claude-argv"
  _stub_claude
  export PATH="$STUB_BIN:$PATH"

  # `wiki path` answering 0 keeps the wiki-capture gate's knowledge-base check
  # from depending on the machine, the way should_wiki_capture.bats does it.
  printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB_BIN/wiki"
  chmod +x "$STUB_BIN/wiki"
}

# _stub_claude — records argv and the inherited sentinel, and behaves the way
# the real CLI does about --bare: with it, the slash command does not resolve.
# A stub that ignored the flag would pass the log assertion below on exactly
# the argv that caused the outage.
_stub_claude() {
  cat > "$STUB_BIN/claude" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" > "$ARGV_LOG"
printf 'WORKBENCH_AUTO_TASK=%s\n' "\${WORKBENCH_AUTO_TASK:-<unset>}" >> "$ARGV_LOG"
for arg in "\$@"; do
  if [[ "\$arg" == "--bare" ]]; then
    for candidate in "\$@"; do
      [[ "\$candidate" == /* ]] && { echo "Unknown command: \$candidate"; exit 0; }
    done
  fi
done
echo "stub ran the skill"
EOF
  chmod +x "$STUB_BIN/claude"
}

teardown() {
  common_teardown
}

# _wait_for <predicate-fn> — polls up to 5s (50 x 0.1s) for a predicate
# function to report success. The script detaches, so every assertion below
# waits for the stub (or its output) to land rather than for a fixed sleep.
_wait_for() {
  local predicate="$1" waited=0
  while ! "$predicate"; do
    [[ "$waited" -ge 50 ]] && return 1
    sleep 0.1
    waited=$((waited + 1))
  done
}

_argv_log_exists() {
  [[ -f "$ARGV_LOG" ]]
}

_wait_for_argv() {
  _wait_for _argv_log_exists
}

_stub_started() {
  [[ -f "$TMPDIR/stub-started" ]]
}

# ── The invocation ───────────────────────────────────────────────────────────

@test "run-auto-task: does not pass --bare" {
  run "$RUN_AUTO_TASK" dream
  [[ "$status" -eq 0 ]]

  _wait_for_argv
  # The regression itself. --bare broke slash-command resolution on the
  # installed build, which is the one thing this script sends.
  ! grep -q -- '--bare' "$ARGV_LOG"
}

@test "run-auto-task: sends the skill as a slash command" {
  run "$RUN_AUTO_TASK" dream
  [[ "$status" -eq 0 ]]

  _wait_for_argv
  grep -q -- '-p /dream' "$ARGV_LOG"
}

@test "run-auto-task: exports the cascade sentinel to the spawned session" {
  run "$RUN_AUTO_TASK" promote
  [[ "$status" -eq 0 ]]

  _wait_for_argv
  grep -q '^WORKBENCH_AUTO_TASK=promote$' "$ARGV_LOG"
}

@test "run-auto-task: returns immediately without waiting for the session" {
  # A Stop hook must not block on a multi-minute agent. The default stub
  # returns in milliseconds regardless of backgrounding, so it can't tell a
  # detached call from a blocking one — swap in a stub that stays alive long
  # enough for the assertion below to catch it still running.
  #
  # Asserted by overlap, not by a stopwatch. An earlier version bounded
  # elapsed wall-clock at 1s against a 3s stub, which fails on a loaded
  # machine for reasons that have nothing to do with backgrounding — it was
  # observed taking 73s under a parallel suite run, reporting a detach bug
  # that did not exist. Whether the caller returned while the child was still
  # running is the actual claim, and it is a fact about two files rather than
  # about how fast this machine happens to be.
  cat > "$STUB_BIN/claude" <<EOF
#!/usr/bin/env bash
touch "\$TMPDIR/stub-started"
sleep 3
touch "\$TMPDIR/stub-finished"
EOF
  chmod +x "$STUB_BIN/claude"

  run "$RUN_AUTO_TASK" retro
  [[ "$status" -eq 0 ]]

  # The child must have been reached — otherwise "still running" below is
  # satisfied by a stub that never started.
  _wait_for _stub_started

  # The caller has already returned (run completed above) while the child is
  # mid-sleep. A blocking call could not observe this state at all.
  [[ ! -f "$TMPDIR/stub-finished" ]]
}

# ── The outcome nobody was asserting ─────────────────────────────────────────

@test "run-auto-task: log does not record an unresolved slash command" {
  # The shape of the six-day failure: exit 0, a log file, a usage row, and
  # `Unknown command: /dream` as the entire transcript. The stub refuses the
  # slash command only when handed --bare, so this fails if the flag returns.
  run "$RUN_AUTO_TASK" dream
  [[ "$status" -eq 0 ]]

  # Wait for content, not for the file. The shell redirect creates it empty
  # before the session writes a byte, so breaking on existence greps nothing
  # and the assertion passes against the very output it is meant to catch.
  local log_file=""
  _dream_log_has_content() {
    log_file="$(find "$CLAUDE_LOG_DIR" -name 'dream-*.log' | head -1)"
    [[ -n "$log_file" && -s "$log_file" ]]
  }
  _wait_for _dream_log_has_content
  [[ -n "$log_file" ]]

  # Fails on the pre-fix behaviour, which is the point of having it.
  ! grep -q 'Unknown command:' "$log_file"
}

# ── Cascade prevention ───────────────────────────────────────────────────────

# _make_due_repo — a registered repo that puts all four gates past their
# cooldown with enough sessions. Every gate must be answering *yes* before the
# sentinel is asked, or a gate that exits 1 for its own reasons — an empty
# sandbox does exactly that — passes whether the sentinel works or not.
_make_due_repo() {
  local repo memory stale encoded
  stale=$(( $(date +%s) - 8 * 24 * 3600 ))

  repo="$(gate_repo "due-project")"
  memory="$(gate_memory "$repo")"
  echo "$stale" > "$memory/.last-dream"
  echo "$stale" > "$memory/.last-promote"

  mkdir -p "$WORKBENCH_STATE_DIR/gates"
  echo "$stale" > "$WORKBENCH_STATE_DIR/gates/last-retro"
  encoded="$(printf '%s' "${repo#/}" | tr -c 'A-Za-z0-9_' '-')"
  echo "$stale" > "$WORKBENCH_STATE_DIR/gates/--${encoded}--.last-wiki-capture"

  gate_sessions "$(gate_claude_dir "$repo")" 12
  printf '%s' "$repo"
}

@test "every gate is due, then the sentinel turns all four off" {
  # Without this the spawned session reaches its own Stop hook, asks the gate
  # that spawned it, gets the same yes — the stamp is only written on
  # completion — and forks again, unbounded.
  local repo gate
  repo="$(_make_due_repo)"

  # Both halves matter. The first proves the fixtures reach the branch under
  # test; without it the second half is vacuous.
  for gate in dream promote retro wiki-capture; do
    run bash -c "cd '$repo' && '$REPO_ROOT/ai/skills/$gate/should-$gate.sh'"
    [[ "$status" -eq 0 ]] || {
      echo "gate $gate is not due before the sentinel is applied (status $status)"
      return 1
    }
  done

  for gate in dream promote retro wiki-capture; do
    run bash -c "cd '$repo' && WORKBENCH_AUTO_TASK='$gate' '$REPO_ROOT/ai/skills/$gate/should-$gate.sh'"
    [[ "$status" -eq 1 ]] || {
      echo "gate $gate returned $status inside an auto-task session"
      return 1
    }
  done
}
