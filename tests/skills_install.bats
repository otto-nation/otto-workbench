#!/usr/bin/env bats
# Tests for step_skills — the cross-harness skill installer.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"

  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_SYNC=true
  mkdir -p "$HOME"

  FAKE_WORKBENCH="$TMPDIR/workbench"
  mkdir -p "$FAKE_WORKBENCH/ai/skills" "$FAKE_WORKBENCH/ai/claude/agents"
  cp "$REPO_ROOT/ai/skills/steps.sh" "$FAKE_WORKBENCH/ai/skills/steps.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _make_skill NAME [AGENT] — writes a minimal skill into the fake source tree.
_make_skill() {
  local name="$1" agent="${2:-}"
  local dir="$FAKE_WORKBENCH/ai/skills/$name"
  mkdir -p "$dir"
  {
    echo "---"
    echo "name: $name"
    echo "description: \"Test skill.\""
    echo "source: otto-workbench/ai/skills/$name/SKILL.md"
    [[ -n "$agent" ]] && echo "agent: $agent" || true
    echo "trigger: \"Use when testing\""
    echo "---"
    echo ""
    echo "# $name"
    [[ -n "$agent" ]] && echo "<!-- AGENT_PROTOCOL_PLACEHOLDER: spliced at install -->" || true
  } > "$dir/SKILL.md"
}

# _make_agent NAME BODY — writes an agent file with frontmatter and a body.
_make_agent() {
  printf -- '---\nname: %s\n---\n%s\n' "$1" "$2" \
    > "$FAKE_WORKBENCH/ai/claude/agents/$1.md"
}

# _run_step — sources the real libraries against the fake workbench and runs the step.
#
# WORKBENCH_STABLE_DIR is pinned to the fake tree as well: lib/constants.sh only
# derives it when unset, so the real checkout's value would leak in from the
# environment and install_symlink would rewrite every source path back to it.
_run_step() {
  run bash -c "
    export WORKBENCH_DIR='$FAKE_WORKBENCH'
    export WORKBENCH_STABLE_DIR='$FAKE_WORKBENCH'
    . '$REPO_ROOT/lib/ui.sh'
    . '$FAKE_WORKBENCH/ai/skills/steps.sh'
    step_skills
  "
}

@test "installs a plain skill into both discovery roots" {
  _make_skill anatomy

  _run_step
  [ "$status" -eq 0 ]
  [ -L "$HOME/.claude/skills/anatomy" ]
  [ -L "$HOME/.agents/skills/anatomy" ]
  [ -f "$HOME/.agents/skills/anatomy/SKILL.md" ]
}

@test "both targets resolve to the one canonical source" {
  _make_skill anatomy

  _run_step
  [ "$(readlink "$HOME/.claude/skills/anatomy")" = "$FAKE_WORKBENCH/ai/skills/anatomy" ]
  [ "$(readlink "$HOME/.agents/skills/anatomy")" = "$FAKE_WORKBENCH/ai/skills/anatomy" ]
}

@test "a user override replaces the default in both roots" {
  _make_skill anatomy
  mkdir -p "$TMPDIR/config/overrides/ai/skills/anatomy"
  echo "override" > "$TMPDIR/config/overrides/ai/skills/anatomy/SKILL.md"

  _run_step
  [ "$(cat "$HOME/.claude/skills/anatomy/SKILL.md")" = "override" ]
  [ "$(cat "$HOME/.agents/skills/anatomy/SKILL.md")" = "override" ]
}

@test "a .disabled sentinel suppresses the skill in both roots" {
  _make_skill anatomy
  mkdir -p "$TMPDIR/config/overrides/ai/skills"
  touch "$TMPDIR/config/overrides/ai/skills/anatomy.disabled"

  _run_step
  [ ! -e "$HOME/.claude/skills/anatomy" ]
  [ ! -e "$HOME/.agents/skills/anatomy" ]
}

@test "a removed skill is pruned from both roots" {
  _make_skill anatomy
  _run_step
  [ -L "$HOME/.claude/skills/anatomy" ]

  rm -rf "$FAKE_WORKBENCH/ai/skills/anatomy"
  _run_step
  [ ! -e "$HOME/.claude/skills/anatomy" ]
  [ ! -e "$HOME/.agents/skills/anatomy" ]
}

@test "an agent-backed skill installs to Pi only, with the protocol spliced in" {
  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"

  _run_step
  [ ! -e "$HOME/.claude/skills/reviewer" ]
  [ -f "$HOME/.agents/skills/reviewer/SKILL.md" ]
  [ ! -L "$HOME/.agents/skills/reviewer" ]
  grep -q "REVIEW PROTOCOL BODY" "$HOME/.agents/skills/reviewer/SKILL.md"
  ! grep -q "AGENT_PROTOCOL_PLACEHOLDER" "$HOME/.agents/skills/reviewer/SKILL.md"
}

@test "the spliced skill keeps its own frontmatter" {
  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"

  _run_step
  grep -q "^name: reviewer" "$HOME/.agents/skills/reviewer/SKILL.md"
}

@test "re-running rewrites the spliced skill rather than appending to it" {
  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"

  _run_step
  _run_step
  [ "$(grep -c "REVIEW PROTOCOL BODY" "$HOME/.agents/skills/reviewer/SKILL.md")" -eq 1 ]
}

@test "an agent-backed skill whose agent is missing is skipped, not half-written" {
  _make_skill reviewer reviewer

  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.agents/skills/reviewer" ]
}

@test "a skill that becomes agent-backed loses its Claude-side symlink" {
  _make_skill reviewer
  _run_step
  [ -L "$HOME/.claude/skills/reviewer" ]

  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"
  _run_step
  [ ! -e "$HOME/.claude/skills/reviewer" ]
  [ -f "$HOME/.agents/skills/reviewer/SKILL.md" ]
}
