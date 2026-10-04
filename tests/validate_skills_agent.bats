#!/usr/bin/env bats
# Tests for validate-skills — agent-backed skills, agent coverage, and the protocols table.
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
