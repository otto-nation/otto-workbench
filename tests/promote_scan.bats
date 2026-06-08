#!/usr/bin/env bats
# Tests for promote-scan Python script — memory and workbench artifact scanning.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  PROMOTE_SCAN="$REPO_ROOT/ai/claude/bin/promote-scan"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: run Python expression importing from promote-scan
_py() {
  python3 -c "
import sys, importlib.util, importlib.machinery
loader = importlib.machinery.SourceFileLoader('promote_scan', '$PROMOTE_SCAN')
spec = importlib.util.spec_from_loader('promote_scan', loader)
mod = importlib.util.module_from_spec(spec)
sys.modules['promote_scan'] = mod
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

# Helper: create a fake workbench directory structure
_make_workbench() {
  local wb="$1"
  mkdir -p "$wb/ai/guidelines/rules"
  mkdir -p "$wb/ai/claude/agents"
  mkdir -p "$wb/ai/memory"
  mkdir -p "$wb/bin"
}

# Helper: create a rule file with a heading
_make_rule() {
  local wb="$1" filename="$2" heading="$3"
  cat > "$wb/ai/guidelines/rules/$filename" <<EOF
# $heading

- Some rule content here
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

@test "parse_frontmatter: returns empty dict for no frontmatter" {
  local tmpfile="$TMPDIR/no_fm.md"
  printf 'Just plain text\n' > "$tmpfile"
  result=$(_py_here <<PY
fm = mod.parse_frontmatter("$tmpfile")
print(len(fm))
PY
)
  [[ "$result" == "0" ]]
}

@test "parse_frontmatter: returns empty dict for missing file" {
  result=$(_py_here <<PY
fm = mod.parse_frontmatter("$TMPDIR/nonexistent.md")
print(len(fm))
PY
)
  [[ "$result" == "0" ]]
}

# ── read_body ─────────────────────────────────────────────────────────────────

@test "read_body: extracts content after frontmatter" {
  local tmpfile="$TMPDIR/body_test.md"
  cat > "$tmpfile" <<'FM'
---
name: test
description: test desc
---

This is the body content.
FM
  result=$(_py_here <<PY
from pathlib import Path
print(mod.read_body(Path("$tmpfile")))
PY
)
  [[ "$result" == "This is the body content." ]]
}

@test "read_body: returns all content when no frontmatter" {
  local tmpfile="$TMPDIR/no_fm_body.md"
  printf 'Plain text content\n' > "$tmpfile"
  result=$(_py_here <<PY
from pathlib import Path
print(mod.read_body(Path("$tmpfile")))
PY
)
  [[ "$result" == "Plain text content" ]]
}

# ── first_heading ─────────────────────────────────────────────────────────────

@test "first_heading: extracts first heading" {
  local tmpfile="$TMPDIR/heading_test.md"
  cat > "$tmpfile" <<'MD'
# My Heading

Some content.

## Sub heading
MD
  result=$(_py_here <<PY
from pathlib import Path
print(mod.first_heading(Path("$tmpfile")))
PY
)
  [[ "$result" == "My Heading" ]]
}

@test "first_heading: returns empty string for no headings" {
  local tmpfile="$TMPDIR/no_heading.md"
  printf 'Just text\n' > "$tmpfile"
  result=$(_py_here <<PY
from pathlib import Path
print(mod.first_heading(Path("$tmpfile")))
PY
)
  [[ "$result" == "" ]]
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
  local dir="$TMPDIR/.claude/projects/test-proj/memory"
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
  local dir="$TMPDIR/.claude/projects/test-proj/memory"
  _make_memory_dir "test-proj" "- [Topic](topic.md) — entry"
  _make_topic_file "$dir" "topic.md" "my-topic" "desc" "Important rule: always use tabs."

  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Important rule: always use tabs."* ]]
}

@test "scan: detects stale entries" {
  local dir="$TMPDIR/.claude/projects/test-proj/memory"
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
  local dir="$TMPDIR/.claude/projects/test-proj/memory"
  _make_memory_dir "test-proj" "- [Topic](topic.md) — entry"
  _make_topic_file "$dir" "topic.md" "topic"
  echo "1717862400" > "$dir/.last-promote"

  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"2024"* ]] || [[ "$output" == *"promote"* ]]
}

@test "scan: handles multiple projects" {
  local dir1="$TMPDIR/.claude/projects/project-one/memory"
  local dir2="$TMPDIR/.claude/projects/project-two/memory"
  _make_memory_dir "project-one" "- [A](a.md) — entry a"
  _make_topic_file "$dir1" "a.md" "topic-a" "First project"
  _make_memory_dir "project-two" "- [B](b.md) — entry b"
  _make_topic_file "$dir2" "b.md" "topic-b" "Second project"

  local wb="$TMPDIR/workbench"
  _make_workbench "$wb"

  run "$PROMOTE_SCAN" --home "$TMPDIR" --workbench "$wb"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"project-one"* ]]
  [[ "$output" == *"project-two"* ]]
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
  local dir="$TMPDIR/.claude/projects/test-proj/memory"
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
