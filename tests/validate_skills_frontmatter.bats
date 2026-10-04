#!/usr/bin/env bats
# Tests for validate-skills — SKILL.md frontmatter conventions and the output_schema field.
setup() {
  load 'test_helper'
  common_setup
  load 'validate_skills_helper'
  VALIDATE_SKILLS="$REPO_ROOT/bin/local/validate-skills"

  FAKE_WORKBENCH="$TMPDIR/workbench"
  mkdir -p "$FAKE_WORKBENCH/ai/skills"
}

teardown() {
  common_teardown
}

@test "resolves its repo root from its own path, not an inherited GIT_DIR" {
  run env GIT_DIR="$TMPDIR/nowhere" GIT_WORK_TREE="$TMPDIR/nowhere" "$VALIDATE_SKILLS" --help
  [ "$status" -eq 0 ]
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
  local dir="$FAKE_WORKBENCH/ai/skills/bad-skill"
  mkdir -p "$dir"
  echo "# No frontmatter" > "$dir/SKILL.md"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing YAML frontmatter"* ]]
}

@test "unclosed frontmatter fails" {
  local dir="$FAKE_WORKBENCH/ai/skills/bad-skill"
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
  local dir="$FAKE_WORKBENCH/ai/skills/no-name"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
description: "Has description"
source: otto-workbench/ai/skills/no-name/SKILL.md
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field: name"* ]]
}

@test "missing description field fails" {
  local dir="$FAKE_WORKBENCH/ai/skills/no-desc"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: no-desc
source: otto-workbench/ai/skills/no-desc/SKILL.md
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field: description"* ]]
}

@test "missing invocation field fails" {
  local dir="$FAKE_WORKBENCH/ai/skills/no-invoc"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: no-invoc
description: "Has description"
source: otto-workbench/ai/skills/no-invoc/SKILL.md
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field: invocation"* ]]
}

@test "missing source field fails" {
  local dir="$FAKE_WORKBENCH/ai/skills/no-source"
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
  local dir="$FAKE_WORKBENCH/ai/skills/no-trigger"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: no-trigger
description: "Has description"
source: otto-workbench/ai/skills/no-trigger/SKILL.md
invocation: "/no-trigger"
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field: trigger"* ]]
}

@test "valid skill with trigger and skip passes" {
  local dir="$FAKE_WORKBENCH/ai/skills/full-skill"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: full-skill
description: "A complete skill"
source: otto-workbench/ai/skills/full-skill/SKILL.md
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
  local dir="$FAKE_WORKBENCH/ai/skills/no-skip"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: no-skip
description: "Has trigger but no skip"
source: otto-workbench/ai/skills/no-skip/SKILL.md
invocation: "/no-skip"
trigger: "Use when testing skip optionality"
---
EOF
  _run_validate
  [ "$status" -eq 0 ]
}

# ── Name mismatch ────────────────────────────────────────────────────────────

@test "name not matching directory fails" {
  local dir="$FAKE_WORKBENCH/ai/skills/actual-dir"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: wrong-name
description: "Test"
source: otto-workbench/ai/skills/actual-dir/SKILL.md
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"does not match directory"* ]]
}

# ── Source mismatch ──────────────────────────────────────────────────────────

@test "source not matching expected path fails" {
  local dir="$FAKE_WORKBENCH/ai/skills/my-skill"
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
  local dir="$FAKE_WORKBENCH/ai/skills/unpaired"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: unpaired
description: "Test"
source: otto-workbench/ai/skills/unpaired/SKILL.md
lifecycle_cadence: "24h"
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"lifecycle_scope missing"* ]]
}

@test "lifecycle_scope without lifecycle_cadence fails" {
  local dir="$FAKE_WORKBENCH/ai/skills/unpaired"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: unpaired
description: "Test"
source: otto-workbench/ai/skills/unpaired/SKILL.md
lifecycle_scope: per-project
---
EOF
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"lifecycle_cadence missing"* ]]
}

@test "invalid lifecycle_scope value fails" {
  local dir="$FAKE_WORKBENCH/ai/skills/bad-scope"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: bad-scope
description: "Test"
source: otto-workbench/ai/skills/bad-scope/SKILL.md
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
  local dir="$FAKE_WORKBENCH/ai/skills/bad-skill"
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
  local dir="$FAKE_WORKBENCH/ai/skills/quoted"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'EOF'
---
name: 'quoted'
description: 'A skill with single-quoted values'
source: 'otto-workbench/ai/skills/quoted/SKILL.md'
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
  mkdir -p "$FAKE_WORKBENCH/ai/skills/empty-skill"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing SKILL.md"* ]]
}

# ── output_schema tool ───────────────────────────────────────────────────────

# Helper: create an executable in the fake workbench's ai/bin/
_make_tool() {
  local name="$1" body="$2"
  local bin_dir="$FAKE_WORKBENCH/ai/bin"
  mkdir -p "$bin_dir"
  printf '#!/usr/bin/env bash\n%s\n' "$body" > "$bin_dir/$name"
  chmod +x "$bin_dir/$name"
}

# Helper: create a SKILL.md declaring an output_schema tool
_make_skill_with_tool() {
  local name="$1" tool="$2"
  local dir="$FAKE_WORKBENCH/ai/skills/$name"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<EOF
---
name: $name
description: "Test skill description."
source: otto-workbench/ai/skills/$name/SKILL.md
invocation: "/$name"
trigger: "Use when testing output_schema"
output_schema:
  tool: $tool
---
EOF
}

@test "output_schema tool emitting a valid schema passes" {
  _make_tool "good-tool" "echo '{\"name\": \"good-tool\", \"input_schema\": {}}'"
  _make_skill_with_tool "schema-skill" "good-tool"
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"supports --tool-schema"* ]]
}

@test "output_schema tool that exits 0 without a schema fails" {
  _make_tool "silent-tool" "exit 0"
  _make_skill_with_tool "schema-skill" "silent-tool"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"invalid --tool-schema document"* ]]
}

# Non-object JSON is rejected by the `type == "object"` check on the emitted document.
@test "output_schema tool emitting a scalar document fails" {
  _make_tool "scalar-tool" "echo 3"
  _make_skill_with_tool "schema-skill" "scalar-tool"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"scalar-tool' emits an invalid --tool-schema document"* ]]
}

@test "output_schema tool emitting an array document fails" {
  _make_tool "array-tool" "echo '[]'"
  _make_skill_with_tool "schema-skill" "array-tool"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"array-tool' emits an invalid --tool-schema document"* ]]
}

@test "output_schema tool emitting JSON without required keys fails" {
  _make_tool "partial-tool" "echo '{\"name\": \"partial-tool\"}'"
  _make_skill_with_tool "schema-skill" "partial-tool"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"invalid --tool-schema document"* ]]
}

@test "output_schema tool exiting non-zero fails" {
  _make_tool "broken-tool" "exit 1"
  _make_skill_with_tool "schema-skill" "broken-tool"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"does not support --tool-schema"* ]]
}

# ── output_schema naming a pr subcommand ─────────────────────────────────────
#
# A skill citing `pr ci` rather than `ai/bin/ci-check` names the invocation its
# reader would type; the shim is an implementation detail of how `pr` used to
# dispatch. These resolve through `cli.schema` in one interpreter rather than
# executing anything, which is why there is no `_make_tool` here.
#
# The fake workbench borrows the real `ai/lib`, because that is the subject:
# the resolver has to reach a genuine `cli.schema`. Nothing tracked is edited
# — only the SKILL.md under $TMPDIR names the subcommand.

_link_real_lib() {
  mkdir -p "$FAKE_WORKBENCH/ai"
  ln -sfn "$REPO_ROOT/ai/lib" "$FAKE_WORKBENCH/ai/lib"
}

@test "output_schema naming a pr subcommand resolves without running anything" {
  _link_real_lib
  _make_skill_with_tool "schema-skill" "pr ci"
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"output_schema tool 'pr ci' supports --tool-schema"* ]]
}

@test "every pr subcommand with a ToolParser delegate resolves" {
  _link_real_lib
  _make_skill_with_tool "rebase-skill" "pr rebase"
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"output_schema tool 'pr rebase' supports --tool-schema"* ]]
}

@test "a pr subcommand that declares no schema is refused" {
  # `pr review` has a delegate parser, but a plain ArgumentParser — it prints
  # prose, so it reports no schema and a skill must not claim one for it.
  _link_real_lib
  _make_skill_with_tool "schema-skill" "pr review"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"'pr review' declares no schema"* ]]
}

@test "an unknown pr subcommand is refused rather than passing silently" {
  _link_real_lib
  _make_skill_with_tool "schema-skill" "pr nosuchcommand"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"'pr nosuchcommand' declares no schema"* ]]
}

@test "a pr subcommand is not looked for in ai/bin" {
  # The old resolver would have reported "not found in ai/bin/" for a name
  # with a space in it. Reaching that message means the branch was skipped.
  _link_real_lib
  _make_skill_with_tool "schema-skill" "pr ci"
  _run_validate
  [[ "$output" != *"not found in ai/bin/"* ]]
}

# ── Mixed valid and invalid ──────────────────────────────────────────────────

@test "mixed valid and invalid reports correct error count" {
  _make_skill "good-skill"
  local dir="$FAKE_WORKBENCH/ai/skills/bad-skill"
  mkdir -p "$dir"
  echo "# No frontmatter" > "$dir/SKILL.md"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"1 of"*"failed"* ]]
}
