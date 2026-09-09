#!/usr/bin/env bats
# Tests for should-wiki-capture.sh — the per-repo passive capture cooldown.
#
# Unlike should-dream.sh this gate answers for the repo the session ran in
# rather than sweeping every project, so the cases here turn on cwd as much as
# on the stamp.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(mktemp -d)"
  export HOME="$TEST_HOME"
  SHOULD_CAPTURE="$REPO_ROOT/ai/skills/wiki-capture/should-wiki-capture.sh"

  # A repo to run the gate from, and a fake `wiki` ahead of the real one so the
  # knowledge-base question is controlled rather than inherited from the
  # machine. The default answers "there is one".
  REPO="$TEST_HOME/repo"
  mkdir -p "$REPO"
  STUB_BIN="$TEST_HOME/stub-bin"
  mkdir -p "$STUB_BIN"
  _stub_wiki 0
  export PATH="$STUB_BIN:$PATH"
}

teardown() {
  rm -rf "$TEST_HOME"
  common_teardown
}

# _stub_wiki EXIT_CODE — makes `wiki path` answer with EXIT_CODE. 2 is the code
# the real one uses for "no knowledge base found".
_stub_wiki() {
  printf '#!/usr/bin/env bash\nexit %s\n' "$1" > "$STUB_BIN/wiki"
  chmod +x "$STUB_BIN/wiki"
}

# _project_dir DIR — the ~/.claude/projects directory Claude Code would name
# for a session rooted at DIR. Spelled out here rather than sourced, so the
# test would catch the helper changing the transform out from under the gate.
_project_dir() {
  printf '%s/.claude/projects/%s' "$HOME" "$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '-')"
}

# _make_sessions COUNT [LAST_CAPTURE_TS] [SESSION_MTIME] — gives the repo a
# project directory with COUNT transcripts, and optionally a capture stamp.
_make_sessions() {
  local count="$1" last_capture="${2:-}" mtime="${3:-}"
  local dir
  dir="$(_project_dir "$REPO")"
  mkdir -p "$dir"

  [ -n "$last_capture" ] && echo "$last_capture" > "$dir/.last-wiki-capture"

  local i
  for i in $(seq 1 "$count"); do
    printf '{"type":"user"}\n' > "$dir/session-$i.jsonl"
    [ -n "$mtime" ] && touch -t "$mtime" "$dir/session-$i.jsonl"
  done
  return 0
}

_run_gate() {
  run bash -c "cd '$REPO' && '$SHOULD_CAPTURE'"
}

# _stale_ts — a stamp comfortably past the gate's 24h interval, and _fresh_ts
# one comfortably inside it. Named here so the relationship to
# CAPTURE_INTERVAL_HOURS in should-wiki-capture.sh is stated once rather than
# copied as a bare offset into every case.
_stale_ts() { printf '%s' "$(( $(date +%s) - 25 * 3600 ))"; }
_fresh_ts() { printf '%s' "$(( $(date +%s) - 3600 ))"; }

# ── Due ──────────────────────────────────────────────────────────────────────

@test "a repo with a wiki, no stamp and enough sessions is due" {
  _make_sessions 3
  _run_gate
  [ "$status" -eq 0 ]
}

@test "a stamp older than the interval is due" {
  # Session count well clear of the minimum, so only the interval is under test.
  _make_sessions 10 "$(_stale_ts)"
  _run_gate
  [ "$status" -eq 0 ]
}

@test "exactly the minimum session count is due" {
  # MIN_SESSIONS is 3, and the boundary is inclusive.
  _make_sessions 3 "$(_stale_ts)"
  _run_gate
  [ "$status" -eq 0 ]
}

# ── Not due ──────────────────────────────────────────────────────────────────

@test "a stamp inside the interval is not due" {
  _make_sessions 10 "$(_fresh_ts)"
  _run_gate
  [ "$status" -eq 1 ]
}

@test "one under the minimum session count is not due" {
  _make_sessions 2
  _run_gate
  [ "$status" -eq 1 ]
}

@test "sessions older than the stamp do not count toward the minimum" {
  _make_sessions 5 "$(_stale_ts)" "202001010000"
  _run_gate
  [ "$status" -eq 1 ]
}

@test "a repo Claude Code has never opened is not due" {
  _run_gate
  [ "$status" -eq 1 ]
}

# ── The knowledge base ───────────────────────────────────────────────────────

@test "a repo with no knowledge base is never due" {
  # `wiki path` exits 2 there, which is the ordinary answer for most repos.
  _stub_wiki 2
  _make_sessions 5
  _run_gate
  [ "$status" -eq 1 ]
}

@test "the knowledge base is only consulted once the cheap gates pass" {
  # `wiki path` is a Python process and this runs on every session exit, so a
  # repo that fails the stamp check must not pay for it. The stub records
  # being called.
  printf '#!/usr/bin/env bash\ntouch "%s/wiki-was-called"\nexit 0\n' "$TEST_HOME" \
    > "$STUB_BIN/wiki"
  chmod +x "$STUB_BIN/wiki"
  _make_sessions 10 "$(_fresh_ts)"
  _run_gate
  [ "$status" -eq 1 ]
  [ ! -f "$TEST_HOME/wiki-was-called" ]
}

# ── Scoping ──────────────────────────────────────────────────────────────────

@test "another project being overdue does not make this one due" {
  # The bug this scoping exists to prevent: run-auto-task inherits the hook's
  # cwd, so a gate firing for someone else's repo captures into this one's
  # knowledge base.
  local other
  other="$(_project_dir "$TEST_HOME/other-repo")"
  mkdir -p "$other"
  local i
  for i in 1 2 3 4 5; do printf '{}\n' > "$other/session-$i.jsonl"; done

  _make_sessions 10 "$(_fresh_ts)"
  _run_gate
  [ "$status" -eq 1 ]
}

@test "the gate reads the stamp for the repo it was run from" {
  _make_sessions 10 "$(_stale_ts)"
  # A fresh stamp on a different repo must not suppress this one.
  local other
  other="$(_project_dir "$TEST_HOME/other-repo")"
  mkdir -p "$other"
  date +%s > "$other/.last-wiki-capture"
  _run_gate
  [ "$status" -eq 0 ]
}

# ── Completion ───────────────────────────────────────────────────────────────

@test "the completion script writes a stamp the gate then honours" {
  _make_sessions 3
  _run_gate
  [ "$status" -eq 0 ]

  run bash -c "cd '$REPO' && '$REPO_ROOT/ai/skills/wiki-capture/wiki-capture-complete.sh'"
  [ "$status" -eq 0 ]

  _run_gate
  [ "$status" -eq 1 ]
}

@test "the completion script is silent when the repo has no project directory" {
  run bash -c "cd '$REPO' && '$REPO_ROOT/ai/skills/wiki-capture/wiki-capture-complete.sh'"
  [ "$status" -eq 0 ]
}

@test "the completion script stamps the directory it was given" {
  _make_sessions 1
  run bash -c "'$REPO_ROOT/ai/skills/wiki-capture/wiki-capture-complete.sh' '$REPO'"
  [ "$status" -eq 0 ]
  [ -f "$(_project_dir "$REPO")/.last-wiki-capture" ]
}

# ── Wiring ───────────────────────────────────────────────────────────────────

@test "the Stop hook runs the gate and the auto-task behind it" {
  run jq -r '.hooks.Stop[0].hooks[].command' "$REPO_ROOT/ai/claude/settings.json"
  [[ "$output" == *"should-wiki-capture.sh && run-auto-task wiki-capture"* ]]
}

@test "the skill the auto-task names exists" {
  [ -f "$REPO_ROOT/ai/skills/wiki-capture/SKILL.md" ]
}
