#!/usr/bin/env bats
# Tests for dream-scan Python script — session signal extraction and memory state reporting.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  DREAM_SCAN="$REPO_ROOT/ai/claude/bin/dream-scan"
}

teardown() {
  rm -rf "$TMPDIR"
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

# ── extract_user_text ─────────────────────────────────────────────────────────

@test "extract_user_text: string content" {
  result=$(_py 'print(mod.extract_user_text({"type":"user","message":{"content":"hello world"}}))')
  [[ "$result" == "hello world" ]]
}

@test "extract_user_text: content blocks" {
  result=$(_py_here <<'PY'
r = {"type":"user","message":{"content":[{"type":"text","text":"hello blocks"}]}}
print(mod.extract_user_text(r))
PY
)
  [[ "$result" == "hello blocks" ]]
}

@test "extract_user_text: skips system reminders" {
  result=$(_py 'print(mod.extract_user_text({"type":"user","message":{"content":"<system-reminder>stuff</system-reminder>"}}))')
  [[ "$result" == "None" ]]
}

@test "extract_user_text: skips image-only content" {
  result=$(_py_here <<'PY'
r = {"type":"user","message":{"content":[{"type":"image","source":{"data":"abc"}}]}}
print(mod.extract_user_text(r))
PY
)
  [[ "$result" == "None" ]]
}

@test "extract_user_text: non-user type returns None" {
  result=$(_py 'print(mod.extract_user_text({"type":"assistant","message":{"content":"hello"}}))')
  [[ "$result" == "None" ]]
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
