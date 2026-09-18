#!/usr/bin/env bats
# Tests for validate-skills — SKILL.md frontmatter conventions.

setup() {
  load 'test_helper'
  common_setup
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

# Helper: create a valid SKILL.md with optional lifecycle fields
_make_skill() {
  local name="$1"
  local cadence="${2:-}"
  local scope="${3:-}"
  local trigger="${4:-Use when testing}"
  local dir="$FAKE_WORKBENCH/ai/skills/$name"
  mkdir -p "$dir"

  {
    echo "---"
    echo "name: $name"
    echo "description: \"Test skill description.\""
    echo "source: otto-workbench/ai/skills/$name/SKILL.md"
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

# ── agent-backed skills ──────────────────────────────────────────────────────

# _make_agent_skill NAME AGENT — a skill backed by an agent file, so it carries
# no invocation of its own.
_make_agent_skill() {
  local name="$1" agent="$2"
  local dir="$FAKE_WORKBENCH/ai/skills/$name"
  mkdir -p "$dir" "$FAKE_WORKBENCH/ai/claude/agents"
  printf -- '---\nname: %s\n---\nbody\n' "$agent" \
    > "$FAKE_WORKBENCH/ai/claude/agents/$agent.md"
  {
    echo "---"
    echo "name: $name"
    echo "description: \"Test skill description.\""
    echo "source: otto-workbench/ai/skills/$name/SKILL.md"
    echo "agent: $agent"
    echo "trigger: \"Use when testing\""
    echo "---"
    echo ""
    echo "# $name"
  } > "$dir/SKILL.md"
}

@test "an agent-backed skill needs no invocation field" {
  _make_agent_skill reviewer reviewer

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE_SKILLS" --quiet
  [ "$status" -eq 0 ]
}

@test "a skill with neither invocation nor agent fails" {
  _make_skill anatomy
  sed -i.bak '/^invocation:/d' "$FAKE_WORKBENCH/ai/skills/anatomy/SKILL.md" \
    && rm -f "$FAKE_WORKBENCH/ai/skills/anatomy/SKILL.md.bak"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE_SKILLS" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field: invocation"* ]]
}

@test "an agent field naming no agent file fails" {
  _make_agent_skill reviewer reviewer
  rm "$FAKE_WORKBENCH/ai/claude/agents/reviewer.md"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE_SKILLS" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"has no ai/claude/agents/reviewer.md"* ]]
}

@test "a name violating the Agent Skills standard fails" {
  _make_skill anatomy
  local dir="$FAKE_WORKBENCH/ai/skills/anatomy"
  mv "$dir" "$FAKE_WORKBENCH/ai/skills/Anatomy--x"
  sed -i.bak -e 's/^name: anatomy/name: Anatomy--x/' \
    -e 's|skills/anatomy/|skills/Anatomy--x/|' \
    "$FAKE_WORKBENCH/ai/skills/Anatomy--x/SKILL.md" \
    && rm -f "$FAKE_WORKBENCH/ai/skills/Anatomy--x/SKILL.md.bak"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE_SKILLS" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"must be lowercase"* ]]
}

@test "a description over 1024 chars fails" {
  _make_skill anatomy
  local long
  long="$(printf 'x%.0s' $(seq 1 1025))"
  sed -i.bak "s|^description:.*|description: \"$long\"|" \
    "$FAKE_WORKBENCH/ai/skills/anatomy/SKILL.md" \
    && rm -f "$FAKE_WORKBENCH/ai/skills/anatomy/SKILL.md.bak"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE_SKILLS" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"caps it at 1024"* ]]
}

# ── Agent coverage ───────────────────────────────────────────────────────────

@test "an agent file with no skill and no allowlist entry fails" {
  # The omission this task fixes: debugger, incident and migrate each had an
  # agent file and no skill for a whole phase, and nothing said so.
  mkdir -p "$FAKE_WORKBENCH/ai/claude/agents"
  printf -- '---\nname: orphan\n---\nbody\n' \
    > "$FAKE_WORKBENCH/ai/claude/agents/orphan.md"

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"orphan"* ]]
}

@test "an agent with a matching skill passes" {
  mkdir -p "$FAKE_WORKBENCH/ai/claude/agents" "$FAKE_WORKBENCH/ai/skills/paired"
  printf -- '---\nname: paired\n---\nbody\n' \
    > "$FAKE_WORKBENCH/ai/claude/agents/paired.md"
  printf -- '---\nname: paired\nagent: paired\n---\nbody\n' \
    > "$FAKE_WORKBENCH/ai/skills/paired/SKILL.md"

  _run_validate --quiet
  [[ "$output" != *"never reaches Pi"* ]]
}

@test "a skill whose agent field names another agent fails" {
  # The filename check this replaced passed on exactly this: the directory is
  # there, so the agent reads as covered, while ai/skills/steps.sh splices the
  # other agent's protocol in and this one reaches Pi nowhere.
  mkdir -p "$FAKE_WORKBENCH/ai/claude/agents" "$FAKE_WORKBENCH/ai/skills/paired"
  printf -- '---\nname: paired\n---\nbody\n' \
    > "$FAKE_WORKBENCH/ai/claude/agents/paired.md"
  printf -- '---\nname: paired\nagent: elsewhere\n---\nbody\n' \
    > "$FAKE_WORKBENCH/ai/skills/paired/SKILL.md"

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"declares agent 'elsewhere'"* ]]
}

@test "a skill directory that declares no agent at all fails" {
  mkdir -p "$FAKE_WORKBENCH/ai/claude/agents" "$FAKE_WORKBENCH/ai/skills/paired"
  printf -- '---\nname: paired\n---\nbody\n' \
    > "$FAKE_WORKBENCH/ai/claude/agents/paired.md"
  printf -- '---\nname: paired\n---\nbody\n' \
    > "$FAKE_WORKBENCH/ai/skills/paired/SKILL.md"

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"declares agent '<none>'"* ]]
}

@test "a programmatic agent needs no skill" {
  mkdir -p "$FAKE_WORKBENCH/ai/claude/agents"
  printf -- '---\nname: changelog\n---\nbody\n' \
    > "$FAKE_WORKBENCH/ai/claude/agents/changelog.md"

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

# ── Agent protocols table parity ────────────────────────────────────────────

_make_protocol_tables() {
  local claude_row="$1" pi_row="$2"
  mkdir -p "$FAKE_WORKBENCH/ai/claude" "$FAKE_WORKBENCH/ai/pi"
  {
    echo "# Claude Code"
    echo ""
    echo "## Agent Protocols"
    echo ""
    echo "| Situation | Agent file | Constraint |"
    echo "|-----------|-----------|------------|"
    echo "$claude_row"
  } > "$FAKE_WORKBENCH/ai/claude/CLAUDE.md"
  {
    echo "# Workbench guidelines"
    echo ""
    echo "## Agent protocols"
    echo ""
    echo "| Situation | Skill | Constraint |"
    echo "|-----------|-------|------------|"
    echo "$pi_row"
  } > "$FAKE_WORKBENCH/ai/pi/AGENTS.head.md"
}

@test "matching protocol tables pass" {
  _make_protocol_tables \
    "| Investigating a bug | \`debugger.md\` | Diagnose before fixing |" \
    "| Investigating a bug | \`debugger\` | Diagnose before fixing |"

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "a protocol table row missing from AGENTS.head.md fails" {
  _make_protocol_tables \
    "| Investigating a bug | \`debugger.md\` | Diagnose before fixing |" \
    "| Investigating a bug | \`debugger\` | Wrong constraint |"

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"Agent protocols table diverges"* ]]
}

# ── Superpowers shim pin ────────────────────────────────────────────────────

# _make_shim NAME PIN RECORDED — a skill carrying the override marker, with the
# pin written into ai/pi/settings.json and RECORDED into the shim's header.
# RECORDED may be empty, for a shim that records no version at all.
# The override comment goes between the frontmatter and the first heading, where
# every real shim carries it — the check reads the header, not the whole file.
_make_shim() {
  local name="$1" pin="$2" recorded="${3:-}"
  local dir="$FAKE_WORKBENCH/ai/skills/$name"
  _make_skill "$name"

  mkdir -p "$FAKE_WORKBENCH/ai/pi"
  cat > "$FAKE_WORKBENCH/ai/pi/settings.json" <<JSON
{
  "packages": [
    "git:github.com/obra/superpowers@$pin"
  ]
}
JSON

  local version_line="     -->"
  [[ -n "$recorded" ]] && version_line="     Written against superpowers $recorded. -->"

  # Insert above the `# NAME` heading _make_skill wrote. awk rather than sed:
  # BSD sed rejects a literal newline in a substitution replacement.
  local tmp="$dir/SKILL.md.tmp"
  awk -v heading="# $name" \
      -v open="<!-- Overrides superpowers:$name, which does it the upstream way." \
      -v version="$version_line" '
    $0 == heading && !done { print open; print version; print ""; done = 1 }
    { print }
  ' "$dir/SKILL.md" > "$tmp"
  mv "$tmp" "$dir/SKILL.md"
}

@test "a shim recording the pinned version passes" {
  _make_shim using-git-worktrees v6.3.0 v6.3.0

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "a shim written against an older version than the pin fails" {
  _make_shim using-git-worktrees v6.4.0 v6.3.0

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"written against superpowers v6.3.0"* ]]
  [[ "$output" == *"pins v6.4.0"* ]]
}

@test "a shim recording no version at all fails" {
  _make_shim using-git-worktrees v6.3.0 ""

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"without recording the version"* ]]
}

@test "every shim is checked, not a hardcoded list" {
  # The check discovers shims by their override marker. A skill added later is
  # covered without editing the validator — which is the property that makes
  # this worth having over the three-name loop it replaces.
  _make_shim using-git-worktrees v6.3.0 v6.3.0
  _make_shim some-future-shim v6.3.0 v6.2.0

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"skills/some-future-shim"* ]]
}

@test "a skill with no override marker needs no recorded version" {
  _make_skill ordinary-skill
  mkdir -p "$FAKE_WORKBENCH/ai/pi"
  echo '{"packages":["git:github.com/obra/superpowers@v6.3.0"]}' \
    > "$FAKE_WORKBENCH/ai/pi/settings.json"

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "shims pass when the package is not declared at all" {
  # The shims are what make the package safe to install, so a tree carrying
  # them before the entry lands is a legitimate mid-adoption state.
  _make_shim using-git-worktrees v6.3.0 v6.3.0
  echo '{"packages":["git:github.com/usemaximum/pi-extensions"]}' \
    > "$FAKE_WORKBENCH/ai/pi/settings.json"

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "an object-form package entry is read for its pin" {
  # Pi accepts an object entry carrying filters; sync-settings.jq identifies
  # entries by source either way, so the pin has to be readable from both.
  _make_shim using-git-worktrees v6.3.0 v6.2.0
  cat > "$FAKE_WORKBENCH/ai/pi/settings.json" <<'JSON'
{
  "packages": [
    { "source": "git:github.com/obra/superpowers@v6.3.0" }
  ]
}
JSON

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"pins v6.3.0"* ]]
}

@test "a version quoted in the body does not stand in for the header" {
  # Only the header is searched. Body prose discussing another version must not
  # satisfy the check for a header that was never updated.
  _make_shim using-git-worktrees v6.4.0 v6.3.0
  cat >> "$FAKE_WORKBENCH/ai/skills/using-git-worktrees/SKILL.md" <<'BODY'

# Using Git Worktrees

Written against superpowers v6.4.0 is the kind of line a migration note carries.
BODY

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"written against superpowers v6.3.0"* ]]
}

# ── Superpowers shim override name ──────────────────────────────────────────

# _retarget_shim NAME CLAIMED — point an existing shim's marker at CLAIMED,
# leaving it in its own directory. This is the shape of the mistake: a shim
# renamed, or copied to seed a second one, with the marker left behind.
_retarget_shim() {
  local name="$1" claimed="$2"
  # Separate declarations: a single `local` evaluates every right-hand side
  # before binding any of the names, so a `file=` referring to `$name` on the
  # same line expands it empty.
  local file="$FAKE_WORKBENCH/ai/skills/$name/SKILL.md"
  local tmp="$file.tmp"
  sed "s/Overrides superpowers:$name,/Overrides superpowers:$claimed,/" \
    "$file" > "$tmp"
  mv "$tmp" "$file"
}

@test "a shim whose marker names another skill fails" {
  # Pi collides on directory name, so this override displaces nothing — the
  # upstream skill keeps answering and the shim is never read.
  _make_shim using-git-worktrees v6.3.0 v6.3.0
  _retarget_shim using-git-worktrees brainstorming

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"claims to override 'brainstorming'"* ]]
  [[ "$output" == *"'using-git-worktrees'"* ]]
}

@test "a shim whose marker matches its directory passes" {
  _make_shim using-git-worktrees v6.3.0 v6.3.0

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "a marker naming no skill at all fails" {
  _make_shim using-git-worktrees v6.3.0 v6.3.0
  _retarget_shim using-git-worktrees ""

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"without naming a skill"* ]]
}

@test "the override name is checked even when the package is not pinned" {
  # The pin check exits early on an undeclared package. A mistargeted marker is
  # wrong on its own terms, so it must still be caught in that state — which is
  # exactly the mid-adoption tree where a shim is most likely to be edited.
  _make_shim using-git-worktrees v6.3.0 v6.3.0
  _retarget_shim using-git-worktrees brainstorming
  echo '{"packages":["git:github.com/usemaximum/pi-extensions"]}' \
    > "$FAKE_WORKBENCH/ai/pi/settings.json"

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"claims to override 'brainstorming'"* ]]
}

@test "every shim's name is checked, not a hardcoded list" {
  _make_shim using-git-worktrees v6.3.0 v6.3.0
  _make_shim some-future-shim v6.3.0 v6.3.0
  _retarget_shim some-future-shim writing-skills

  _run_validate --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"skills/some-future-shim"* ]]
}

@test "a skill with no override marker is not name-checked" {
  # The check keys on the marker. An ordinary skill whose name happens to match
  # nothing upstream must not be dragged into it.
  _make_skill ordinary-skill

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "prose naming another override does not stand in for the marker" {
  # Only the header is searched, as with the pin. A body sentence mentioning a
  # sibling override must not be read as this shim's marker.
  _make_shim using-git-worktrees v6.3.0 v6.3.0
  cat >> "$FAKE_WORKBENCH/ai/skills/using-git-worktrees/SKILL.md" <<'BODY'

See also: Overrides superpowers:brainstorming, handled by its own shim.
BODY

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

# ── lifecycle cadence vs the hook that gates it ──────────────────────────────

# Writes a should-*.sh beside a skill. Only the two constants are read, so the
# body is whatever makes the file plausible.
_make_hook() {
  local name="$1" hours="$2" sessions="${3:-}"
  local dir="$FAKE_WORKBENCH/ai/skills/$name"
  mkdir -p "$dir"
  {
    echo "#!/usr/bin/env bash"
    echo "UPPER_INTERVAL_HOURS=$hours"
    [[ -n "$sessions" ]] && echo "MIN_SESSIONS=$sessions"
    # Not an early exit: a false [[ ]] as the last statement would become the
    # function's exit status and fail the calling test under set -e whenever
    # sessions is empty. See bash.md's function-last-statement pitfall.
    return 0
  } > "$dir/should-$name.sh"
}

@test "a cadence matching its hook passes" {
  _make_skill widget "24h" per-project
  _make_hook widget 24

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "a cadence disagreeing with its hook fails" {
  _make_skill widget "24h" per-project
  _make_hook widget 72

  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"disagrees"* ]]
}

@test "a day-form cadence is compared in hours" {
  # "7 days" and 168 are the same bound written two ways.
  _make_skill widget "7 days" per-project
  _make_hook widget 168

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "a skill omitting its hook's session floor fails" {
  # The drift this check exists for: every lifecycle skill advertised only its
  # interval, so all four read as firing on a timer when none of them does.
  _make_skill widget "24h" per-project
  _make_hook widget 24 5

  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"MIN_SESSIONS=5"* ]]
}

@test "a skill stating its hook's session floor passes" {
  _make_skill widget "24h" per-project "Auto-triggers once 24h and 5 sessions have both passed"
  _make_hook widget 24 5

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "a bare number near the word session does not satisfy the floor" {
  # dream passed this check on the sentence "a session from March 15" before the
  # pattern required the number to stand before the noun.
  _make_skill widget "24h" per-project "Convert relative dates: yesterday in a session from March 5 becomes absolute"
  _make_hook widget 24 5

  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"MIN_SESSIONS=5"* ]]
}

@test "a skill with no should-script is not checked for cadence agreement" {
  # machine's cadence lives in a generator, not a gate — there is nothing to
  # disagree with.
  _make_skill widget "24h" global

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "a larger number ending in the floor's digit does not satisfy it" {
  # "25 sessions" must not satisfy MIN_SESSIONS=5 on its last digit — a skill
  # documenting the wrong number is the case this check exists to catch.
  _make_skill widget "24h" per-project "Auto-triggers once 24h and 25 sessions have passed"
  _make_hook widget 24 5

  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"MIN_SESSIONS=5"* ]]
}

@test "an unparseable cadence is reported as unrecognized, not as 0h" {
  # Bash arithmetic reads a non-numeric prefix as 0, which would otherwise
  # report "(0h) disagrees" and send the reader hunting a constant mismatch.
  _make_skill widget "several days" per-project
  _make_hook widget 24

  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"not a recognized duration"* ]]
  [[ "$output" != *"(0h)"* ]]
}

@test "a fractional day-form cadence fails cleanly instead of aborting the run" {
  # A case glob pins only the characters it names, so "3.5 days" reached $(( ))
  # as a syntax error and set -e took the whole run down — every other skill
  # left unchecked, with no diagnostic. The second skill here must still be
  # reported.
  _make_skill widget "3.5 days" per-project
  _make_hook widget 24
  _make_skill gadget "24h" per-project
  _make_hook gadget 72

  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"not a recognized duration"* ]]
  [[ "$output" == *"gadget"* ]]
}

# ── Skill script paths ───────────────────────────────────────────────────────

@test "a skill invoking its script through Claude's root fails" {
  # Both roots symlink to the same source, so a Claude-rooted path works on a
  # machine with Claude installed and is simply absent under Pi. Nothing else
  # reports it: the agent runs a path that is not there.
  _make_skill widget
  echo 'bash ~/.claude/skills/widget/widget-complete.sh' \
    >> "$FAKE_WORKBENCH/ai/skills/widget/SKILL.md"

  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"harness-specific root"* ]]
}

@test "a skill invoking its script through the shared root passes" {
  _make_skill widget
  echo 'bash ~/.agents/skills/widget/widget-complete.sh' \
    >> "$FAKE_WORKBENCH/ai/skills/widget/SKILL.md"

  _run_validate --quiet
  [ "$status" -eq 0 ]
}

@test "a \$HOME-spelled Claude root is caught too" {
  _make_skill widget
  echo 'bash $HOME/.claude/skills/widget/widget-complete.sh' \
    >> "$FAKE_WORKBENCH/ai/skills/widget/SKILL.md"

  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"harness-specific root"* ]]
}

@test "a Claude root on a line that also names the shared one is still caught" {
  # The compliant reference must not launder the bad one beside it: the filter
  # drops non-compliant matches, not whole lines that happen to hold a good one.
  _make_skill widget
  echo 'was ~/.claude/skills/widget/widget.sh, now ~/.agents/skills/widget/widget.sh' \
    >> "$FAKE_WORKBENCH/ai/skills/widget/SKILL.md"

  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *"harness-specific root"* ]]
}

@test "prose naming both skills roots is not a script path" {
  # writing-skills names both in a sentence about where skills install. The
  # filename at the end of the pattern is what keeps the check off it.
  _make_skill widget
  echo 'Skills install into `~/.claude/skills/` and `~/.agents/skills/`.' \
    >> "$FAKE_WORKBENCH/ai/skills/widget/SKILL.md"

  _run_validate --quiet
  [ "$status" -eq 0 ]
}
