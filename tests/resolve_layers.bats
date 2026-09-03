#!/usr/bin/env bats
# Tests for resolve_layers() and user override integration in lib/files.sh.

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"

  TMPDIR="$(mktemp -d)"
  BASE_DIR="$TMPDIR/base"
  USER_DIR="$TMPDIR/user"
  mkdir -p "$BASE_DIR" "$USER_DIR"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# ─── resolve_layers ──────────────────────────────────────────────────────────

@test "resolve_layers: base only — all files included" {
  echo "default" > "$BASE_DIR/foo.md"
  echo "default" > "$BASE_DIR/bar.md"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*.md" result

  [[ ${#result[@]} -eq 2 ]]
  [[ "${result[foo.md]}" == "$BASE_DIR/foo.md" ]]
  [[ "${result[bar.md]}" == "$BASE_DIR/bar.md" ]]
}

@test "resolve_layers: user file replaces base file with same name" {
  echo "default" > "$BASE_DIR/foo.md"
  echo "override" > "$USER_DIR/foo.md"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*.md" result

  [[ ${#result[@]} -eq 1 ]]
  [[ "${result[foo.md]}" == "$USER_DIR/foo.md" ]]
}

@test "resolve_layers: user adds new files not in base" {
  echo "default" > "$BASE_DIR/foo.md"
  echo "new" > "$USER_DIR/custom.md"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*.md" result

  [[ ${#result[@]} -eq 2 ]]
  [[ "${result[foo.md]}" == "$BASE_DIR/foo.md" ]]
  [[ "${result[custom.md]}" == "$USER_DIR/custom.md" ]]
}

@test "resolve_layers: .disabled sentinel suppresses base file" {
  echo "default" > "$BASE_DIR/foo.md"
  echo "default" > "$BASE_DIR/bar.md"
  touch "$USER_DIR/foo.disabled"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*.md" result

  [[ ${#result[@]} -eq 1 ]]
  [[ "${result[bar.md]}" == "$BASE_DIR/bar.md" ]]
  [[ -z "${result[foo.md]+set}" ]]
}

@test "resolve_layers: empty user dir — falls through to base" {
  echo "default" > "$BASE_DIR/foo.md"
  rmdir "$USER_DIR"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*.md" result

  [[ ${#result[@]} -eq 1 ]]
  [[ "${result[foo.md]}" == "$BASE_DIR/foo.md" ]]
}

@test "resolve_layers: works with directory glob" {
  mkdir -p "$BASE_DIR/skill-a" "$BASE_DIR/skill-b"
  mkdir -p "$USER_DIR/skill-a"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*/" result

  [[ ${#result[@]} -eq 2 ]]
  # User dir wins for skill-a
  [[ "${result[skill-a]}" == "$USER_DIR/skill-a" ]]
  [[ "${result[skill-b]}" == "$BASE_DIR/skill-b" ]]
}

@test "resolve_layers: .disabled suppresses directory items" {
  mkdir -p "$BASE_DIR/skill-a" "$BASE_DIR/skill-b"
  touch "$USER_DIR/skill-a.disabled"

  local -A result
  resolve_layers "$BASE_DIR" "$USER_DIR" "*/" result

  [[ ${#result[@]} -eq 1 ]]
  [[ "${result[skill-b]}" == "$BASE_DIR/skill-b" ]]
}

# ─── resolve_rules ───────────────────────────────────────────────────────────

# The three roots resolve_rules reads. Pointed at the sandbox rather than the
# real ones so a rule on the machine running the suite cannot reach the result.
_rule_layers() {
  GUIDELINES_RULES_SRC_DIR="$TMPDIR/repo"
  GENERATED_RULES_DIR="$TMPDIR/generated"
  USER_RULES_DIR="$TMPDIR/override"
  RULES_GLOB="*.md"
  mkdir -p "$GUIDELINES_RULES_SRC_DIR" "$GENERATED_RULES_DIR" "$USER_RULES_DIR"
}

@test "resolve_rules: all three layers are merged" {
  _rule_layers
  echo "repo" > "$GUIDELINES_RULES_SRC_DIR/general.md"
  echo "generated" > "$GENERATED_RULES_DIR/workbench.md"
  echo "override" > "$USER_RULES_DIR/testing.local.md"

  local -A result
  resolve_rules result

  [[ ${#result[@]} -eq 3 ]]
  [[ "${result[general.md]}" == "$GUIDELINES_RULES_SRC_DIR/general.md" ]]
  [[ "${result[workbench.md]}" == "$GENERATED_RULES_DIR/workbench.md" ]]
  [[ "${result[testing.local.md]}" == "$USER_RULES_DIR/testing.local.md" ]]
}

@test "resolve_rules: the generated layer beats the repo default it shadows" {
  _rule_layers
  echo "repo" > "$GUIDELINES_RULES_SRC_DIR/workbench.md"
  echo "generated" > "$GENERATED_RULES_DIR/workbench.md"

  local -A result
  resolve_rules result

  [[ "${result[workbench.md]}" == "$GENERATED_RULES_DIR/workbench.md" ]]
}

# The layer an operator writes by hand is last, so it wins a name against either
# layer below it — the generated one included, which is the leg the two-pass
# merge exists to get right.
@test "resolve_rules: an override beats the generated rule it shadows" {
  _rule_layers
  echo "generated" > "$GENERATED_RULES_DIR/workbench.md"
  echo "override" > "$USER_RULES_DIR/workbench.md"

  local -A result
  resolve_rules result

  [[ ${#result[@]} -eq 1 ]]
  [[ "${result[workbench.md]}" == "$USER_RULES_DIR/workbench.md" ]]
}

@test "resolve_rules: an override .disabled sentinel suppresses a generated rule" {
  _rule_layers
  echo "generated" > "$GENERATED_RULES_DIR/workbench.md"
  touch "$USER_RULES_DIR/workbench.disabled"

  local -A result
  resolve_rules result

  [[ ${#result[@]} -eq 0 ]]
}

@test "resolve_rules: an absent generated layer resolves the other two" {
  _rule_layers
  rmdir "$GENERATED_RULES_DIR"
  echo "repo" > "$GUIDELINES_RULES_SRC_DIR/general.md"
  echo "override" > "$USER_RULES_DIR/testing.local.md"

  local -A result
  resolve_rules result

  [[ ${#result[@]} -eq 2 ]]
  [[ "${result[general.md]}" == "$GUIDELINES_RULES_SRC_DIR/general.md" ]]
  [[ "${result[testing.local.md]}" == "$USER_RULES_DIR/testing.local.md" ]]
}

# ─── is_disabled ─────────────────────────────────────────────────────────────

@test "is_disabled: returns true when sentinel exists" {
  touch "$USER_DIR/foo.disabled"
  is_disabled "$USER_DIR" "foo"
}

@test "is_disabled: returns false when no sentinel" {
  ! is_disabled "$USER_DIR" "foo"
}

# ─── skill_agent ─────────────────────────────────────────────────────────────

# _skill_file NAME CONTENT — writes a SKILL.md and prints its path.
_skill_file() {
  mkdir -p "$BASE_DIR/$1"
  printf '%s' "$2" > "$BASE_DIR/$1/SKILL.md"
  printf '%s' "$BASE_DIR/$1/SKILL.md"
}

@test "skill_agent: prints the declared agent" {
  local f
  f="$(_skill_file reviewer '---
name: reviewer
agent: reviewer
---
body
')"
  [ "$(skill_agent "$f")" = "reviewer" ]
}

@test "skill_agent: prints nothing for a skill declaring none" {
  local f
  f="$(_skill_file anatomy '---
name: anatomy
---
body
')"
  [ -z "$(skill_agent "$f")" ]
}

@test "skill_agent: strips quotes around the value" {
  local f
  f="$(_skill_file reviewer '---
agent: "reviewer"
---
')"
  [ "$(skill_agent "$f")" = "reviewer" ]
}

# The divergence this helper exists to close: a `---` rule in the body opens no
# frontmatter block, so an `agent:` line in a skill's prose is documentation and
# must not route the skill away from Claude Code.
@test "skill_agent: ignores an agent line below a horizontal rule in the body" {
  local f
  f="$(_skill_file anatomy '---
name: anatomy
---

# anatomy

---
agent: reviewer
---
')"
  [ -z "$(skill_agent "$f")" ]
}

@test "skill_agent: ignores a body that opens with no frontmatter at all" {
  local f
  f="$(_skill_file anatomy 'agent: reviewer
')"
  [ -z "$(skill_agent "$f")" ]
}

# Callers glob skill directories, so they meet a half-written one before its
# SKILL.md exists. Answering empty keeps them on the skip path rather than on
# awk's exit 2, which under set -e would abort a whole sync or export.
@test "skill_agent: answers empty for a file that is not there" {
  run skill_agent "$BASE_DIR/ghost/SKILL.md"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}
