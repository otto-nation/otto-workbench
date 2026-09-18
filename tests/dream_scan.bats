#!/usr/bin/env bats
# Tests for dream-scan Python script — session signal extraction and memory state reporting.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  DREAM_SCAN="$REPO_ROOT/ai/bin/dream-scan"
  sandbox_state_dir
}

teardown() {
  common_teardown
}

# Helper: run Python expression importing from dream-scan
_py() {
  python3 -c "
import sys, importlib.util, importlib.machinery
loader = importlib.machinery.SourceFileLoader('dream_scan', '$DREAM_SCAN')
spec = importlib.util.spec_from_loader('dream_scan', loader)
mod = importlib.util.module_from_spec(spec)
sys.modules['dream_scan'] = mod
spec.loader.exec_module(mod)
$1
"
}

# Helper: like _py but reads code from stdin
_py_here() {
  local code
  code=$(cat)
  _py "$code"
}

# Helper: create a session JSONL file with user messages
_make_session_jsonl() {
  local dest="$1"
  shift
  mkdir -p "$(dirname "$dest")"
  for msg in "$@"; do
    printf '{"type":"user","message":{"role":"user","content":"%s"}}\n' "$msg"
  done > "$dest"
}

# Helper: create a session JSONL with content-blocks style message
_make_session_jsonl_blocks() {
  local dest="$1" text="$2"
  mkdir -p "$(dirname "$dest")"
  cat > "$dest" <<EOF
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"$text"}]}}
EOF
}

# Helper: create a memory directory with MEMORY.md and topic files
_make_memory_dir() {
  local project="$1" memory_content="$2"
  shift 2
  local dir="$TMPDIR/.claude/projects/$project/memory"
  mkdir -p "$dir"
  printf '%s\n' "$memory_content" > "$dir/MEMORY.md"

  local arg filename content
  for arg in "$@"; do
    filename="${arg%%:*}"
    content="${arg#*:}"
    printf '%s\n' "$content" > "$dir/$filename"
  done
}

# Helper: create a topic file with frontmatter
_make_topic_file() {
  local dir="$1" filename="$2" name="$3" desc="${4:-test entry}" body="${5:-}"
  cat > "$dir/$filename" <<EOF
---
name: $name
description: $desc
metadata:
  type: feedback
---

$body
EOF
}

# ── Reading turns ────────────────────────────────────────────────────────────
# Record shapes, automation filtering and per-record dating belong to
# core/sessions.py and are covered by tests/sessions_test.py. What is asserted
# here is that dream-scan reads through it — that both harnesses reach the
# report, and that a pipeline's own preamble does not.

@test "scan: reads Pi sessions as well as Claude's" {
  mkdir -p "$TMPDIR/.pi/agent/sessions/--repo--"
  cat > "$TMPDIR/.pi/agent/sessions/--repo--/s.jsonl" <<'PI'
{"type":"message","timestamp":"2026-09-16T14:30:00.000Z","message":{"role":"user","content":[{"type":"text","text":"actually that approach is wrong"}],"timestamp":1789575000000}}
PI

  run "$DREAM_SCAN" --home "$TMPDIR" --days 3650
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"actually"* ]]
}

@test "scan: an agent preamble is not a signal" {
  # The bug the automation filter exists for: "You are an adversarial
  # reviewer" matches the correction pattern, and a week of review runs
  # buried every real signal under hundreds of these.
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/agent.jsonl" \
    "You are an adversarial reviewer. Your job is to FALSIFY each finding"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  [[ "$output" != *"adversarial"* ]]
}

# Per-record dating is covered in tests/sessions_test.py, which computes local
# midnight rather than writing a UTC literal — the same assertion spelled here
# would pass or fail on the machine's timezone offset.

@test "scan: files a repo's signals under one id across harnesses" {
  # The same repo worked in from both harnesses is one project, not two: the
  # harnesses' own directory names encode differently, so the cwd each records
  # is what they are grouped by.
  mkdir -p "$TMPDIR/.claude/projects/dash-slug" "$TMPDIR/.pi/agent/sessions/--other--"
  cat > "$TMPDIR/.claude/projects/dash-slug/c.jsonl" <<'CLAUDE'
{"type":"user","cwd":"/repo","timestamp":"2026-09-16T10:00:00.000Z","message":{"role":"user","content":"I prefer the first approach"}}
CLAUDE
  cat > "$TMPDIR/.pi/agent/sessions/--other--/p.jsonl" <<'PI'
{"type":"message","cwd":"/repo","timestamp":"2026-09-16T11:00:00.000Z","message":{"role":"user","content":[{"type":"text","text":"I prefer the second approach"}],"timestamp":1789575000000}}
PI

  run "$DREAM_SCAN" --home "$TMPDIR" --days 3650
  [[ "$status" -eq 0 ]]
  # One id for both, named for the cwd rather than either directory name.
  [[ "$output" == *"--repo--"* ]]
  [[ "$output" != *"dash-slug"* ]]
}

@test "scan: reports a per-harness transcript count" {
  # A harness that stops being discovered reads as a zero here rather than as
  # a corpus that quietly halved.
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/s.jsonl" "I prefer tabs"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Sessions Scanned"* ]]
  [[ "$output" == *"claude 1"* ]]
  [[ "$output" == *"pi 0"* ]]
}

# ── classify_signal ──────────────────────────────────────────────────────────

@test "classify_signal: correction patterns" {
  result=$(_py "print(mod.classify_signal(\"actually, that's wrong\"))")
  [[ "$result" == "correction" ]]
}

@test "classify_signal: preference patterns" {
  result=$(_py 'print(mod.classify_signal("I prefer tabs over spaces"))')
  [[ "$result" == "preference" ]]
}

@test "classify_signal: decision patterns" {
  result=$(_py "print(mod.classify_signal(\"let's go with option A\"))")
  [[ "$result" == "decision" ]]
}

@test "classify_signal: pattern patterns" {
  result=$(_py 'print(mod.classify_signal("you keep forgetting this"))')
  [[ "$result" == "pattern" ]]
}

@test "classify_signal: review feedback patterns" {
  result=$(_py 'print(mod.classify_signal("that is a false positive"))')
  [[ "$result" == "review_feedback" ]]
}

@test "classify_signal: no match returns None" {
  result=$(_py 'print(mod.classify_signal("please read this file for me"))')
  [[ "$result" == "None" ]]
}

@test "classify_signal: case insensitive" {
  result=$(_py 'print(mod.classify_signal("I PREFER spaces"))')
  [[ "$result" == "preference" ]]
}

# ── parse_frontmatter ────────────────────────────────────────────────────────

@test "parse_frontmatter: extracts name, description, type" {
  local tmpfile="$TMPDIR/test_topic.md"
  cat > "$tmpfile" <<'FM'
---
name: my-topic
description: a test topic
metadata:
  type: feedback
---

Body content here.
FM
  result=$(_py_here <<PY
fm = mod.parse_frontmatter("$tmpfile")
print(fm.get("name"), fm.get("description"))
PY
)
  [[ "$result" == "my-topic a test topic" ]]
}

# ── CLI ───────────────────────────────────────────────────────────────────────

@test "dream-scan --help exits 0" {
  run "$DREAM_SCAN" --help
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"usage"* ]] || [[ "$output" == *"Usage"* ]]
}

@test "dream-scan -h exits 0" {
  run "$DREAM_SCAN" -h
  [[ "$status" -eq 0 ]]
}

@test "dream-scan --days accepts integer" {
  run "$DREAM_SCAN" --days 3 --home "$TMPDIR"
  [[ "$status" -eq 0 ]]
}

# The two exit-early flags are a public surface: ai/skills/architecture/SKILL.md
# and ai/skills/dream/SKILL.md shell out to them instead of globbing a harness
# directory. A skill's glob failing is silent, so these assert the contract the
# skills read rather than only the resolvers behind it (tests/sessions_ssot.bats).

@test "dream-scan --memory-dir prints the repo's memory directory and nothing else" {
  # Dots and underscores are the case the skill's own transform got wrong.
  run "$DREAM_SCAN" --home "$TMPDIR" --memory-dir /Users/dev/git/otto.io/feat_one
  [[ "$status" -eq 0 ]]
  [[ "${#lines[@]}" -eq 1 ]]
  [[ "$output" == "$TMPDIR/.claude/projects/-Users-dev-git-otto-io-feat-one/memory" ]]
}

@test "dream-scan --memory-dir exits before the report" {
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/s.jsonl" "I prefer tabs"

  run "$DREAM_SCAN" --home "$TMPDIR" --memory-dir /Users/dev/git/repo
  [[ "$status" -eq 0 ]]
  [[ "$output" != *"Session Signals"* ]]
}

@test "dream-scan --list-transcripts prints one path per line across harnesses" {
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/c.jsonl" "I prefer tabs"
  _make_session_jsonl "$TMPDIR/.pi/agent/sessions/--repo--/p.jsonl" "I prefer spaces"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30 --list-transcripts
  [[ "$status" -eq 0 ]]
  [[ "${#lines[@]}" -eq 2 ]]
  [[ "$output" == *"/.claude/projects/test-proj/c.jsonl"* ]]
  [[ "$output" == *"/.pi/agent/sessions/--repo--/p.jsonl"* ]]
  [[ "$output" != *"Session Signals"* ]]
}

# ── Session scanning (integration) ──────────────────────────────────────────

@test "scan: finds correction in session file" {
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/session1.jsonl" \
    "actually, that approach is wrong"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Session Signals"* ]]
  [[ "$output" == *"correction"* ]]
  [[ "$output" == *"actually"* ]]
}

@test "scan: skips subagent directories" {
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/abc123/subagents/agent-xyz.jsonl" \
    "actually, that's wrong"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  # Should NOT contain the signal from the subagent
  [[ "$output" != *"actually"* ]]
}

@test "scan: respects --days filter" {
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/recent.jsonl" \
    "I prefer tabs"
  # Make a file that's old
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/old.jsonl" \
    "actually, that's wrong"
  touch -t 202501010000 "$TMPDIR/.claude/projects/test-proj/old.jsonl"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 7
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"I prefer"* ]]
  [[ "$output" != *"actually"* ]]
}

@test "scan: truncates long messages to 500 chars" {
  local long_msg
  long_msg="I prefer $(printf 'x%.0s' $(seq 1 600))"
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/long.jsonl" "$long_msg"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  # Output should not contain the full 600+ char message
  [[ ${#output} -lt 1000 ]]
}

@test "scan: handles content blocks format" {
  _make_session_jsonl_blocks "$TMPDIR/.claude/projects/test-proj/blocks.jsonl" \
    "I prefer using content blocks"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"I prefer"* ]]
}

# ── Memory state reporting ────────────────────────────────────────────────────

@test "scan: reports memory state" {
  local dir="$TMPDIR/.claude/projects/test-proj/memory"
  _make_memory_dir "test-proj" "- [Topic A](topic-a.md) — entry a
- [Topic B](topic-b.md) — entry b"
  _make_topic_file "$dir" "topic-a.md" "topic-a" "First topic"
  _make_topic_file "$dir" "topic-b.md" "topic-b" "Second topic"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Memory State"* ]]
  [[ "$output" == *"topic-a"* ]]
  [[ "$output" == *"topic-b"* ]]
}

@test "scan: reads topic file frontmatter" {
  local dir="$TMPDIR/.claude/projects/test-proj/memory"
  _make_memory_dir "test-proj" "- [My Topic](topic.md) — entry"
  _make_topic_file "$dir" "topic.md" "my-topic-name" "A detailed description"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"my-topic-name"* ]]
}

@test "scan: detects stale entries" {
  local dir="$TMPDIR/.claude/projects/test-proj/memory"
  _make_memory_dir "test-proj" "- [Old](old.md) — stale entry"
  _make_topic_file "$dir" "old.md" "old-topic" "Old content"
  # Set mtime to 100 days ago
  touch -t 202502280000 "$dir/old.md"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"stale"* ]] || [[ "$output" == *"STALE"* ]] || [[ "$output" == *">90"* ]]
}

@test "scan: reports last dream timestamp" {
  local dir="$TMPDIR/.claude/projects/test-proj/memory"
  _make_memory_dir "test-proj" "- [Topic](topic.md) — entry"
  _make_topic_file "$dir" "topic.md" "topic"
  echo "1717862400" > "$dir/.last-dream"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"2024"* ]] || [[ "$output" == *"dream"* ]]
}

# ── Output format ─────────────────────────────────────────────────────────────

@test "scan: output has Memory State and Session Signals sections" {
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/s.jsonl" \
    "I prefer this approach"
  local dir="$TMPDIR/.claude/projects/test-proj/memory"
  _make_memory_dir "test-proj" "- [Topic](topic.md) — entry"
  _make_topic_file "$dir" "topic.md" "topic"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"## Memory State"* ]]
  [[ "$output" == *"## Session Signals"* ]]
}

@test "scan: corrections appear before preferences in output" {
  _make_session_jsonl "$TMPDIR/.claude/projects/test-proj/s.jsonl" \
    "I prefer tabs" "actually that is wrong"

  run "$DREAM_SCAN" --home "$TMPDIR" --days 30
  [[ "$status" -eq 0 ]]

  # Find positions of correction and preference sections
  local correction_pos preference_pos
  correction_pos=$(echo "$output" | grep -n "correction" | head -1 | cut -d: -f1)
  preference_pos=$(echo "$output" | grep -n "preference" | head -1 | cut -d: -f1)
  [[ "$correction_pos" -lt "$preference_pos" ]]
}
