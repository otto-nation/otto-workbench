#!/usr/bin/env bats
# Tests for validate-skills — SKILL.md frontmatter conventions.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATE_SKILLS="$REPO_ROOT/bin/local/validate-skills"

  FAKE_WORKBENCH="$TMPDIR/workbench"
  mkdir -p "$FAKE_WORKBENCH/ai/claude/skills"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: create a valid SKILL.md with optional lifecycle fields
_make_skill() {
  local name="$1"
  local cadence="${2:-}"
  local scope="${3:-}"
  local trigger="${4:-Use when testing}"
  local dir="$FAKE_WORKBENCH/ai/claude/skills/$name"
  mkdir -p "$dir"

  {
    echo "---"
    echo "name: $name"
    echo "description: \"Test skill description.\""
    echo "source: otto-workbench/ai/claude/skills/$name/SKILL.md"
    echo "invocation: \"/$name\""
    echo "trigger: \"$trigger\""
    [[ -n "$cadence" ]] && echo "lifecycle_cadence: \"$cadence\""
    [[ -n "$scope" ]] && echo "lifecycle_scope: $scope"
    echo "---"
    echo ""
    echo "# $name"
  } > "$dir/SKILL.md"
}

_run_validate() {
  WORKBENCH_DIR="$FAKE_WORKBENCH" NO_COLOR=1 run "$VALIDATE_SKILLS" "$@"
}

# ── CLI ──────────────────────────────────────────────────────────────────────

@test "validate-skills --help exits 0" {
  run "$VALIDATE_SKILLS" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"SKILL.md"* ]]
}

@test "validate-skills -h exits 0" {
  run "$VALIDATE_SKILLS" -h
  [ "$status" -eq 0 ]
}

# ── No skills ────────────────────────────────────────────────────────────────

@test "no skills exits 0" {
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"no skill directories"* ]]
}

# ── Valid skills ─────────────────────────────────────────────────────────────

@test "valid skill passes all checks" {
  _make_skill "my-skill"
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"passed"* ]]
}

@test "valid skill with lifecycle fields passes" {
  _make_skill "my-skill" "24h" "per-project"
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"lifecycle fields paired"* ]]
  [[ "$output" == *"lifecycle_scope valid"* ]]
}

@test "multiple valid skills all pass" {
  _make_skill "skill-a"
  _make_skill "skill-b" "7 days" "global"
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"passed"*"2 skills"* ]]
}

# ── Missing frontmatter ─────────────────────────────────────────────────────

@test "missing frontmatter fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/bad-skill"
  mkdir -p "$dir"
  echo "# No frontmatter" > "$dir/SKILL.md"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing YAML frontmatter"* ]]
}

@test "unclosed frontmatter fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/bad-skill"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: bad-skill
description: "Missing closing fence"
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing closing ---"* ]]
}

# ── Missing required fields ──────────────────────────────────────────────────

@test "missing name field fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/no-name"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
description: "Has description"
source: otto-workbench/ai/claude/skills/no-name/SKILL.md
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field: name"* ]]
}

@test "missing description field fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/no-desc"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: no-desc
source: otto-workbench/ai/claude/skills/no-desc/SKILL.md
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field: description"* ]]
}

@test "missing invocation field fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/no-invoc"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: no-invoc
description: "Has description"
source: otto-workbench/ai/claude/skills/no-invoc/SKILL.md
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field: invocation"* ]]
}

@test "missing source field fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/no-source"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: no-source
description: "Has description"
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field: source"* ]]
}

@test "missing trigger field fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/no-trigger"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: no-trigger
description: "Has description"
source: otto-workbench/ai/claude/skills/no-trigger/SKILL.md
invocation: "/no-trigger"
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field: trigger"* ]]
}

@test "valid skill with trigger and skip passes" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/full-skill"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: full-skill
description: "A complete skill"
source: otto-workbench/ai/claude/skills/full-skill/SKILL.md
invocation: "/full-skill"
trigger: "Use when the user asks for full skill functionality"
skip: "Do not use for partial operations"
---
EOF
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"trigger field present"* ]]
}

@test "skill without skip field passes" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/no-skip"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: no-skip
description: "Has trigger but no skip"
source: otto-workbench/ai/claude/skills/no-skip/SKILL.md
invocation: "/no-skip"
trigger: "Use when testing skip optionality"
---
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

# ── Name mismatch ────────────────────────────────────────────────────────────

@test "name not matching directory fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/actual-dir"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: wrong-name
description: "Test"
source: otto-workbench/ai/claude/skills/actual-dir/SKILL.md
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"does not match directory"* ]]
}

# ── Source mismatch ──────────────────────────────────────────────────────────

@test "source not matching expected path fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/my-skill"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: my-skill
description: "Test"
source: wrong/path/SKILL.md
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"does not match expected"* ]]
}

# ── Lifecycle field pairing ──────────────────────────────────────────────────

@test "lifecycle_cadence without lifecycle_scope fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/unpaired"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: unpaired
description: "Test"
source: otto-workbench/ai/claude/skills/unpaired/SKILL.md
lifecycle_cadence: "24h"
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"lifecycle_scope missing"* ]]
}

@test "lifecycle_scope without lifecycle_cadence fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/unpaired"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: unpaired
description: "Test"
source: otto-workbench/ai/claude/skills/unpaired/SKILL.md
lifecycle_scope: per-project
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"lifecycle_cadence missing"* ]]
}

@test "invalid lifecycle_scope value fails" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/bad-scope"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: bad-scope
description: "Test"
source: otto-workbench/ai/claude/skills/bad-scope/SKILL.md
lifecycle_cadence: "24h"
lifecycle_scope: invalid
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"must be 'per-project' or 'global'"* ]]
}

# ── Quiet mode ───────────────────────────────────────────────────────────────

@test "--quiet suppresses per-check output but shows summary" {
  _make_skill "my-skill"
  _run_validate --quiet
  [ "$status" -eq 0 ]
  [[ "$output" == *"passed"* ]]
  [[ "$output" != *"frontmatter present"* ]]
}

@test "--quiet with failure exits 1 and shows summary" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/bad-skill"
  mkdir -p "$dir"
  echo "# No frontmatter" > "$dir/SKILL.md"
  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"failed"* ]]
  # Quiet mode still shows errors but not per-check pass marks
  [[ "$output" != *"✓"* ]]
}

# ── Single-quoted values ─────────────────────────────────────────────────────

@test "single-quoted field values are stripped correctly" {
  local dir="$FAKE_WORKBENCH/ai/claude/skills/quoted"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: 'quoted'
description: 'A skill with single-quoted values'
source: 'otto-workbench/ai/claude/skills/quoted/SKILL.md'
invocation: '/quoted'
trigger: 'Use when testing quote handling'
---
EOF
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"name matches directory"* ]]
}

# ── Missing SKILL.md ─────────────────────────────────────────────────────────

@test "skill directory without SKILL.md fails" {
  mkdir -p "$FAKE_WORKBENCH/ai/claude/skills/empty-skill"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing SKILL.md"* ]]
}

# ── Mixed valid and invalid ──────────────────────────────────────────────────

@test "mixed valid and invalid reports correct error count" {
  _make_skill "good-skill"
  local dir="$FAKE_WORKBENCH/ai/claude/skills/bad-skill"
  mkdir -p "$dir"
  echo "# No frontmatter" > "$dir/SKILL.md"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"1 of"*"failed"* ]]
}
