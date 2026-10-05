#!/usr/bin/env bats
# Tests for the promote-scan CLI. In-process cases live in tests/memory_promote_test.py and tests/memory_test.py.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  PROMOTE_SCAN="$REPO_ROOT/ai/bin/promote-scan"
  sandbox_state_dir
  # The store hangs off the data root and is keyed by repo identity, so the
  # root and the registry the scan reads forward from are both sandboxed.
  export WORKBENCH_DATA_DIR="$TMPDIR/data"
  mkdir -p "$WORKBENCH_STATE_DIR"
}

teardown() {
  common_teardown
}

# Helper: create a memory directory with MEMORY.md and topic files
# _make_memory_dir REPO_NAME CONTENT [file:content ...] — a registered repo
# with memory, keyed as production keys it. Sets `dir` for the cases that add
# topic files to it.
_make_memory_dir() {
  local project="$1" memory_content="$2"
  shift 2
  local repo="$TMPDIR/repos/$project"
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '%s\t%s\n' "$repo" "$(cd "$repo" && cd "$(git rev-parse --git-common-dir)" && pwd -P)" \
    >> "$WORKBENCH_STATE_DIR/projects.registry"
  dir="$(gate_memory "$repo")"
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

# Helper: create a fake workbench directory structure
_make_workbench() {
  local wb="$1"
  mkdir -p "$wb/ai/guidelines/rules"
  mkdir -p "$wb/ai/claude/agents"
  mkdir -p "$wb/ai/memory"
  mkdir -p "$wb/bin"
}

# Helper: create a rule file with a heading and optional body
_make_rule() {
  local wb="$1" filename="$2" heading="$3" body="${4:-- Some rule content here}"
  cat > "$wb/ai/guidelines/rules/$filename" <<EOF
# $heading

$body
EOF
}

# Helper: create an agent file with a heading
_make_agent() {
  local wb="$1" filename="$2" heading="$3"
  cat > "$wb/ai/claude/agents/$filename" <<EOF
# $heading

Agent protocol here.
EOF
}

# Helper: create a settings.json with hooks
_make_settings() {
  local wb="$1"
  cat > "$wb/ai/claude/settings.json" <<'EOF'
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write",
        "command": "echo written"
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "command": "echo stopped"
      }
    ]
  }
}
EOF
}

# ── CLI ───────────────────────────────────────────────────────────────────────

@test "promote-scan --help exits 0" {
  run "$PROMOTE_SCAN" --help
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"usage"* ]] || [[ "$output" == *"Usage"* ]]
}

@test "promote-scan -h exits 0" {
  run "$PROMOTE_SCAN" -h
  [[ "$status" -eq 0 ]]
}

@test "promote-scan: runs with empty directories" {
  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Memory State"* ]]
  [[ "$output" == *"Workbench Artifacts"* ]]
}

# ── Memory scanning ──────────────────────────────────────────────────────────

@test "scan: reports memory state with topic files" {
  _make_memory_dir "test-proj" "- [Topic A](topic-a.md) — entry a"
  _make_topic_file "$dir" "topic-a.md" "topic-a" "First topic" "Body of topic A."

  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Memory State"* ]]
  [[ "$output" == *"topic-a"* ]]
  [[ "$output" == *"First topic"* ]]
  [[ "$output" == *"Body of topic A."* ]]
}

@test "scan: includes body content from topic files" {
  _make_memory_dir "test-proj" "- [Topic](topic.md) — entry"
  _make_topic_file "$dir" "topic.md" "my-topic" "desc" "Important rule: always use tabs."

  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Important rule: always use tabs."* ]]
}

@test "scan: detects stale entries" {
  _make_memory_dir "test-proj" "- [Old](old.md) — stale entry"
  _make_topic_file "$dir" "old.md" "old-topic" "Old content"
  touch -t 202502280000 "$dir/old.md"

  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"STALE"* ]]
}

@test "scan: reports last promote timestamp" {
  _make_memory_dir "test-proj" "- [Topic](topic.md) — entry"
  _make_topic_file "$dir" "topic.md" "topic"
  echo "1717862400" > "$(gate_stamp "$TMPDIR/repos/test-proj" last-promote)"

  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  # Asserted on the rendered date alone: an `|| [[ $output == *promote* ]]`
  # arm matches the report's own heading and passes whether or not the stamp
  # was ever found.
  [[ "$output" == *"2024-06-08"* ]]
}

@test "scan: handles multiple projects" {
  local dir1 dir2
  _make_memory_dir "project-one" "- [A](a.md) — entry a"
  dir1="$dir"
  _make_topic_file "$dir1" "a.md" "topic-a" "First project"
  _make_memory_dir "project-two" "- [B](b.md) — entry b"
  dir2="$dir"
  _make_topic_file "$dir2" "b.md" "topic-b" "Second project"

  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  # Both repos are counted and both their topics are rendered, which is what
  # "handles multiple" means. The report prints no project id, so asserting on
  # one would pass on a scan that found a single repo twice.
  [[ "$output" == *"Found 2 project(s) with memory"* ]]
  [[ "$output" == *"topic-a"* ]]
  [[ "$output" == *"topic-b"* ]]
}

# ── Backed-up memories ────────────────────────────────────────────────────────

@test "scan: reports backed-up memories" {
  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"
  _make_topic_file "$wb/ai/memory" "backup.md" "backed-up-topic" "Backed up entry" "Old memory content."

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Backed-Up Memories"* ]]
  [[ "$output" == *"backed-up-topic"* ]]
  [[ "$output" == *"Old memory content."* ]]
}

@test "scan: shows no backed-up memories when directory is empty" {
  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"No backed-up memories found"* ]]
}

# ── Workbench artifact scanning ──────────────────────────────────────────────

@test "scan: reports rules" {
  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"
  _make_rule "$wb" "bash.md" "Bash / Shell"
  _make_rule "$wb" "security.md" "Security"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Rules"* ]]
  [[ "$output" == *"bash.md"* ]]
  [[ "$output" == *"Bash / Shell"* ]]
  [[ "$output" == *"security.md"* ]]
  [[ "$output" == *"Security"* ]]
}

@test "scan: rules include body content" {
  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"
  _make_rule "$wb" "bash.md" "Bash / Shell" "- Scripts should be quiet on success"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"bash.md"* ]]
  [[ "$output" == *"Scripts should be quiet on success"* ]]
}

@test "scan: reports scripts" {
  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"
  touch "$wb/bin/my-script"
  touch "$wb/bin/another-script"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Scripts"* ]]
  [[ "$output" == *"my-script"* ]]
  [[ "$output" == *"another-script"* ]]
}

@test "scan: reports hooks from settings.json" {
  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"
  _make_settings "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Hooks"* ]]
  [[ "$output" == *"PostToolUse"* ]]
  [[ "$output" == *"Stop"* ]]
  [[ "$output" == *"Write"* ]]
}

@test "scan: reports agents" {
  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"
  _make_agent "$wb" "debugger.md" "Debugger Agent"
  _make_agent "$wb" "reviewer.md" "Reviewer Agent"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Agents"* ]]
  [[ "$output" == *"debugger.md"* ]]
  [[ "$output" == *"Debugger Agent"* ]]
  [[ "$output" == *"reviewer.md"* ]]
  [[ "$output" == *"Reviewer Agent"* ]]
}

@test "scan: handles missing settings.json gracefully" {
  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"No hooks found"* ]]
}

# ── Output format ─────────────────────────────────────────────────────────────

@test "scan: output has all major sections" {
  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"## Memory State"* ]]
  [[ "$output" == *"## Backed-Up Memories"* ]]
  [[ "$output" == *"## Workbench Artifacts"* ]]
  [[ "$output" == *"### Rules"* ]]
  [[ "$output" == *"### Scripts"* ]]
  [[ "$output" == *"### Hooks"* ]]
  [[ "$output" == *"### Agents"* ]]
}

@test "scan: full report with all data types" {
  _make_memory_dir "test-proj" "- [Topic](topic.md) — entry"
  _make_topic_file "$dir" "topic.md" "my-topic" "Topic desc" "Topic body."

  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"
  _make_rule "$wb" "general.md" "General"
  _make_agent "$wb" "debugger.md" "Debugger"
  _make_settings "$wb"
  touch "$wb/bin/my-script"
  _make_topic_file "$wb/ai/memory" "archive.md" "archived" "Old memory"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"my-topic"* ]]
  [[ "$output" == *"archived"* ]]
  [[ "$output" == *"general.md"* ]]
  [[ "$output" == *"my-script"* ]]
  [[ "$output" == *"PostToolUse"* ]]
  [[ "$output" == *"debugger.md"* ]]
}
