#!/usr/bin/env bats
# Tests for dream-verify — memory file integrity verification.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR"
  export NO_COLOR=1
  DREAM_VERIFY="$REPO_ROOT/ai/claude/bin/dream-verify"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: create a memory directory with MEMORY.md and optional topic files.
# Usage: _make_memory_dir <project-name> <memory-md-content> [topic-file:content ...]
_make_memory_dir() {
  local project="$1"
  local memory_content="$2"
  shift 2

  local dir="$HOME/.claude/projects/$project/memory"
  mkdir -p "$dir"
  printf '%s\n' "$memory_content" > "$dir/MEMORY.md"

  local arg filename content
  for arg in "$@"; do
    filename="${arg%%:*}"
    content="${arg#*:}"
    printf '%s\n' "$content" > "$dir/$filename"
  done
}

# Helper: create a topic file with frontmatter.
_make_topic_file() {
  local dir="$1"
  local filename="$2"
  local name="$3"
  local body="${4:-}"
  cat > "$dir/$filename" <<EOF
---
name: $name
description: test entry
metadata:
  type: feedback
---

$body
EOF
}

# ── CLI ───────────────────────────────────────────────────────────────────────

@test "dream-verify --help exits 0" {
  run "$DREAM_VERIFY" --help
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Usage"* ]]
}

@test "dream-verify -h exits 0" {
  run "$DREAM_VERIFY" -h
  [[ "$status" -eq 0 ]]
}

# ── Passing checks ───────────────────────────────────────────────────────────

@test "dream-verify: passes when all checks clean" {
  local dir="$HOME/.claude/projects/test-project/memory"
  _make_memory_dir "test-project" "- [Topic](topic.md) — a test entry"
  _make_topic_file "$dir" "topic.md" "my-topic" "Some content here."

  run "$DREAM_VERIFY"
  [[ "$status" -eq 0 ]]
}

@test "dream-verify: passes with multiple clean projects" {
  local dir1="$HOME/.claude/projects/project-one/memory"
  local dir2="$HOME/.claude/projects/project-two/memory"

  _make_memory_dir "project-one" "- [Topic A](topic-a.md) — entry a"
  _make_topic_file "$dir1" "topic-a.md" "topic-a" "Clean content."

  _make_memory_dir "project-two" "- [Topic B](topic-b.md) — entry b"
  _make_topic_file "$dir2" "topic-b.md" "topic-b" "Also clean."

  run "$DREAM_VERIFY"
  [[ "$status" -eq 0 ]]
}

# ── MEMORY.md line count ─────────────────────────────────────────────────────

@test "dream-verify: fails on MEMORY.md over 200 lines" {
  # Build 201 lines: 1 real reference + 200 padding lines
  local dir="$HOME/.claude/projects/bloated-project/memory"
  mkdir -p "$dir"
  python3 -c "
lines = ['- [Entry](entry.md) — real entry']
lines += ['- padding line %d' % i for i in range(200)]
with open('$dir/MEMORY.md', 'w') as f:
    f.write('\n'.join(lines) + '\n')
"
  _make_topic_file "$dir" "entry.md" "entry"

  run "$DREAM_VERIFY"
  [[ "$status" -eq 1 ]]
  [[ "$output" == *"200"* ]]
}

# ── Broken references ────────────────────────────────────────────────────────

@test "dream-verify: fails on broken reference" {
  _make_memory_dir "broken-refs" "- [Missing](missing.md) — does not exist"

  run "$DREAM_VERIFY"
  [[ "$status" -eq 1 ]]
  [[ "$output" == *"missing.md"* ]]
}

# ── Relative dates ───────────────────────────────────────────────────────────

@test "dream-verify: fails on relative date in topic file" {
  local dir="$HOME/.claude/projects/date-project/memory"
  _make_memory_dir "date-project" "- [Topic](topic.md) — has dates"
  _make_topic_file "$dir" "topic.md" "topic" "This happened yesterday and it was bad."

  run "$DREAM_VERIFY"
  [[ "$status" -eq 1 ]]
  [[ "$output" == *"yesterday"* ]]
}

@test "dream-verify: detects multiple relative date words" {
  local dir="$HOME/.claude/projects/multi-date/memory"
  _make_memory_dir "multi-date" "- [Topic](topic.md) — dates"
  _make_topic_file "$dir" "topic.md" "topic" "We discussed this last week and will follow up tomorrow."

  run "$DREAM_VERIFY"
  [[ "$status" -eq 1 ]]
  # Should catch at least one of the patterns
  [[ "$output" == *"last week"* ]] || [[ "$output" == *"tomorrow"* ]]
}

# ── Duplicate names ──────────────────────────────────────────────────────────

@test "dream-verify: fails on duplicate name frontmatter" {
  local dir="$HOME/.claude/projects/dup-project/memory"
  _make_memory_dir "dup-project" \
    "- [First](first.md) — entry one
- [Second](second.md) — entry two"

  _make_topic_file "$dir" "first.md" "same-name" "First file."
  _make_topic_file "$dir" "second.md" "same-name" "Second file with same name."

  run "$DREAM_VERIFY"
  [[ "$status" -eq 1 ]]
  [[ "$output" == *"same-name"* ]]
}

# ── Multi-project failures ───────────────────────────────────────────────────

@test "dream-verify: reports failures from multiple projects" {
  local dir1="$HOME/.claude/projects/clean-project/memory"
  local dir2="$HOME/.claude/projects/broken-project/memory"

  _make_memory_dir "clean-project" "- [Good](good.md) — clean"
  _make_topic_file "$dir1" "good.md" "good" "All fine."

  _make_memory_dir "broken-project" "- [Missing](missing.md) — broken"

  run "$DREAM_VERIFY"
  [[ "$status" -eq 1 ]]
  [[ "$output" == *"missing.md"* ]]
}

# ── Edge cases ───────────────────────────────────────────────────────────────

@test "dream-verify: skips directories without MEMORY.md" {
  mkdir -p "$HOME/.claude/projects/no-memory-project"
  _make_memory_dir "good-project" "- [Topic](topic.md) — entry"
  local dir="$HOME/.claude/projects/good-project/memory"
  _make_topic_file "$dir" "topic.md" "topic" "Content."

  run "$DREAM_VERIFY"
  [[ "$status" -eq 0 ]]
}
