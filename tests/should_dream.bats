#!/usr/bin/env bats
# Tests for should-dream.sh — per-repo dream cooldown checks.
#
# The gate sweeps the project registry and counts a repo's sessions across
# every harness and every worktree, so the fixtures here are registered repos
# rather than bare directories under `.claude/projects`.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # Fully resolved: on macOS mktemp hands back a /var/folders path that git
  # reports as /private/var/folders, and the gate encodes the path git gives it
  # into the directory it looks for the memory in.
  TEST_HOME="$(cd "$(mktemp -d)" && pwd -P)"
  export HOME="$TEST_HOME"
  gate_sandbox
  SHOULD_DREAM="$REPO_ROOT/ai/skills/dream/should-dream.sh"
}

teardown() {
  rm -rf "$TEST_HOME"
  common_teardown
}

# _make_project NAME LAST_DREAM_TS NUM_SESSIONS [SESSION_MTIME]
# A registered repo with a memory directory, a dream stamp and Claude sessions.
# An empty LAST_DREAM_TS leaves no stamp, which is the first-run case.
_make_project() {
  local name="$1" last_dream_ts="$2" num_sessions="$3" session_mtime="${4:-}"
  local repo memory
  repo="$(gate_repo "$name")"
  memory="$(gate_memory "$repo")"

  if [ -n "$last_dream_ts" ]; then
    echo "$last_dream_ts" > "$memory/.last-dream"
  fi

  gate_sessions "$(gate_claude_dir "$repo")" "$num_sessions" "$session_mtime"
}

# ── Per-repo cooldown logic ──────────────────────────────────────────────────

@test "should-dream: project A recent, project B overdue with enough sessions → fires" {
  local now
  now=$(date +%s)
  local two_hours_ago=$((now - 7200))
  local forty_eight_hours_ago=$((now - 172800))

  _make_project "project-a" "$two_hours_ago" 10
  _make_project "project-b" "$forty_eight_hours_ago" 6

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

@test "should-dream: both projects dreamed recently → does not fire" {
  local now
  now=$(date +%s)
  local two_hours_ago=$((now - 7200))

  _make_project "project-a" "$two_hours_ago" 10
  _make_project "project-b" "$two_hours_ago" 10

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

@test "should-dream: project overdue on time but only 2 sessions → does not fire" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  _make_project "project-a" "$forty_eight_hours_ago" 2

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

@test "should-dream: no .last-dream files (first run) with enough sessions → fires" {
  _make_project "new-project" "" 6

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

@test "should-dream: single project, overdue, enough sessions → fires" {
  local now
  now=$(date +%s)
  local thirty_hours_ago=$((now - 108000))

  _make_project "solo-project" "$thirty_hours_ago" 7

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

@test "should-dream: no projects at all → does not fire" {
  mkdir -p "$HOME/.claude/projects"

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

# ── Edge cases ───────────────────────────────────────────────────────────────

@test "should-dream: sessions older than last dream are not counted" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  # Overdue on time, but all 6 sessions have mtimes before the last dream.
  _make_project "stale-project" "$forty_eight_hours_ago" 6 "202001010000"

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

@test "should-dream: exactly at threshold (24h) does not fire" {
  local now
  now=$(date +%s)
  # Just under 24 hours ago (23h 59m)
  local just_under=$((now - 86340))

  _make_project "borderline" "$just_under" 10

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

@test "should-dream: exactly 5 sessions meets minimum" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  _make_project "exact-min" "$forty_eight_hours_ago" 5

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

@test "should-dream: 4 sessions does not meet minimum" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  _make_project "under-min" "$forty_eight_hours_ago" 4

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

# ── Repos without memory/ ────────────────────────────────────────────────────

@test "should-dream: repo without memory/ dir is ignored even with many sessions" {
  local repo
  repo="$(gate_repo "no-memory-project")"
  gate_sessions "$(gate_claude_dir "$repo")" 20

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

@test "should-dream: repo without memory/ does not block eligible repo" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  local no_mem
  no_mem="$(gate_repo "aaa-no-memory")"
  gate_sessions "$(gate_claude_dir "$no_mem")" 20

  _make_project "zzz-has-memory" "$forty_eight_hours_ago" 6

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

# ── Harness and worktree coverage ────────────────────────────────────────────
# The bug this gate was rebuilt for: sessions counted under Claude Code only,
# in the one directory named for the repo root. Interactive work moved to Pi
# and spread across worktrees, and the gate stopped firing for months.

@test "should-dream: Pi sessions count toward the minimum" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  local repo memory
  repo="$(gate_repo "pi-only")"
  memory="$(gate_memory "$repo")"
  echo "$forty_eight_hours_ago" > "$memory/.last-dream"
  gate_sessions "$(gate_pi_dir "$repo")" 6

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

@test "should-dream: sessions spread across worktrees count toward one repo" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  local repo memory
  repo="$(gate_repo "spread")"
  memory="$(gate_memory "$repo")"
  echo "$forty_eight_hours_ago" > "$memory/.last-dream"

  # Two sessions in each of three worktrees: under the minimum alone, over it
  # together. Counting any single directory would leave this gate shut.
  gate_sessions "$(gate_claude_dir "$repo/main")" 2
  gate_sessions "$(gate_claude_dir "$repo/feature-a")" 2
  gate_sessions "$(gate_pi_dir "$repo/feature-b")" 2

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

@test "should-dream: an unregistered repo's memory is not swept" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  # A memory directory and sessions, but no registry line — the repo has never
  # had a workbench command run in it, so nothing knows the path behind the
  # slug. Running any workbench command there is what makes it visible.
  local orphan="$TEST_HOME/unregistered"
  mkdir -p "$(gate_claude_dir "$orphan")/memory"
  echo "$forty_eight_hours_ago" > "$(gate_claude_dir "$orphan")/memory/.last-dream"
  gate_sessions "$(gate_claude_dir "$orphan")" 20

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

# ── The completion script and the gate agree on the set ──────────────────────

@test "dream-complete settles the gate it is paired with" {
  local now
  now=$(date +%s)
  _make_project "project-a" "$((now - 172800))" 6

  run "$SHOULD_DREAM"
  [ "$status" -eq 0 ]

  run "$REPO_ROOT/ai/skills/dream/dream-complete.sh"
  [ "$status" -eq 0 ]

  run "$SHOULD_DREAM"
  [ "$status" -eq 1 ]
}

@test "dream-complete stamps every registered repo with memory" {
  local now
  now=$(date +%s)
  _make_project "project-a" "$((now - 172800))" 6
  _make_project "project-b" "$((now - 172800))" 6

  run "$REPO_ROOT/ai/skills/dream/dream-complete.sh"
  [ "$status" -eq 0 ]
  [ -f "$(gate_memory "$TEST_HOME/project-a")/.last-dream" ]
  [ -f "$(gate_memory "$TEST_HOME/project-b")/.last-dream" ]
}

@test "dream-complete writes no stamp an unregistered repo would keep" {
  # The gate skips a repo that has left the registry, so a completion script
  # globbing memory directories would leave a stamp nothing ever reads back —
  # and the repo would look freshly dreamed to a later reader of that file.
  local orphan="$TEST_HOME/unregistered"
  mkdir -p "$(gate_claude_dir "$orphan")/memory"

  run "$REPO_ROOT/ai/skills/dream/dream-complete.sh"
  [ "$status" -eq 0 ]
  [ ! -f "$(gate_claude_dir "$orphan")/memory/.last-dream" ]
}

@test "dream-complete is quiet with nothing registered" {
  run "$REPO_ROOT/ai/skills/dream/dream-complete.sh"
  [ "$status" -eq 0 ]
}
