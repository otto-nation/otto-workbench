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
# `set -e` matches every production caller: bin/otto-workbench:51 and ai/setup.sh:16
# both set it, so a command that returns non-zero mid-loop aborts the run there.
# Without it here the suite would exercise the step under shell options nothing
# in production uses, and could not see a partial install at all.
#
# WORKBENCH_STABLE_DIR is pinned to the fake tree as well: lib/constants.sh only
# derives it when unset, so the real checkout's value would leak in from the
# environment and install_symlink would rewrite every source path back to it.
_run_step() {
  run bash -c "
    set -e
    export WORKBENCH_DIR='$FAKE_WORKBENCH'
    export WORKBENCH_STABLE_DIR='$FAKE_WORKBENCH'
    . '$REPO_ROOT/lib/ui.sh'
    . '$FAKE_WORKBENCH/ai/skills/steps.sh'
    step_skills
  "
}

# _run_step_from_worktree — runs the step the way every non-main checkout does,
# with WORKBENCH_STABLE_DIR naming a different tree from WORKBENCH_DIR.
#
# install_symlink rewrites each source path through the stable dir, so in this
# configuration — the everyday one on a worktree-based machine — no installed
# symlink points at WORKBENCH_DIR at all. Pinning the two together, as the other
# helpers do, hides whether ownership is decided against the path actually
# written.
_run_step_from_worktree() {
  run bash -c "
    set -e
    export WORKBENCH_DIR='$FAKE_WORKBENCH'
    export WORKBENCH_STABLE_DIR='$TMPDIR/main'
    . '$REPO_ROOT/lib/ui.sh'
    . '$FAKE_WORKBENCH/ai/skills/steps.sh'
    step_skills
  "
}

# _run_step_interrupted — runs the step with a zero file-size limit, which is the
# one way to stop _install_agent_skill between its two writes from the outside.
#
# Creating an empty file is still permitted under the cap, so the marker write
# succeeds and the splice — the only thing here that writes bytes — dies on the
# limit and takes the step down with it under set -e. That asymmetry is what makes
# the write order observable: whichever of the two runs first is the one that
# survives, so a directory left behind carrying the marker means the marker went
# first, and one left behind without it means it did not.
_run_step_interrupted() {
  run bash -c "
    set -e
    export WORKBENCH_DIR='$FAKE_WORKBENCH'
    export WORKBENCH_STABLE_DIR='$FAKE_WORKBENCH'
    . '$REPO_ROOT/lib/ui.sh'
    . '$FAKE_WORKBENCH/ai/skills/steps.sh'
    ulimit -f 0
    step_skills
  "
}

# _run_summary — installs skills, then prints the standalone-tool summary.
# print_skills_summary calls _print_item_list, which lives in the real
# ai/claude/steps.sh rather than the fake workbench's copy — ai/setup.sh
# always sources every ai/*/steps.sh ahead of running any tool's steps, so
# this mirrors that ordering rather than the fake tree's own layout.
_run_summary() {
  run bash -c "
    set -e
    export WORKBENCH_DIR='$FAKE_WORKBENCH'
    export WORKBENCH_STABLE_DIR='$FAKE_WORKBENCH'
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/ai/claude/steps.sh'
    . '$FAKE_WORKBENCH/ai/skills/steps.sh'
    step_skills >/dev/null
    print_skills_summary
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
  # -e follows the symlink, so it is already false for a dangling symlink
  # whether or not the prune actually removed the symlink file itself. -L
  # is what proves the entry is gone rather than merely broken.
  [ ! -e "$HOME/.claude/skills/anatomy" ]
  [ ! -L "$HOME/.claude/skills/anatomy" ]
  [ ! -e "$HOME/.agents/skills/anatomy" ]
  [ ! -L "$HOME/.agents/skills/anatomy" ]
}

@test "a hand-placed symlink outside both layer roots survives a prune, warned about" {
  _make_skill anatomy
  _run_step

  # Dangling on purpose: ownership must be decided from the link text itself
  # (readlink), not by resolving the target through the filesystem, so a
  # symlink pointing nowhere still gets the operator's-not-ours treatment.
  ln -s "$TMPDIR/not-a-layer-root/mynote.md" "$HOME/.claude/skills/mysymlink"

  _run_step
  [ "$status" -eq 0 ]
  [ -L "$HOME/.claude/skills/mysymlink" ]
  [[ "$output" == *"$HOME/.claude/skills/mysymlink was not installed by the workbench"* ]]
}

@test "a dangling symlink into a retired source path inside the workbench is pruned" {
  _make_skill anatomy
  _run_step

  # What an earlier release of this step left on real machines: a symlink it
  # wrote itself, pointing at a source directory that has since moved. The
  # target no longer exists and is under no current layer root, but it is
  # inside the checkout, so the workbench owns the leftover and clears it.
  ln -s "$FAKE_WORKBENCH/ai/claude/skills/context" "$HOME/.claude/skills/context"

  _run_step
  [ "$status" -eq 0 ]
  [ ! -L "$HOME/.claude/skills/context" ]
  [[ "$output" != *"was not installed by the workbench"* ]]
}

@test "a dangling symlink into the retired override path is pruned" {
  _make_skill anatomy
  _run_step

  ln -s "$TMPDIR/config/overrides/ai/claude/skills/context" \
    "$HOME/.agents/skills/context"

  _run_step
  [ "$status" -eq 0 ]
  [ ! -L "$HOME/.agents/skills/context" ]
  [[ "$output" != *"was not installed by the workbench"* ]]
}

@test "a hand-placed symlink to a non-skill file in the checkout survives a prune" {
  _make_skill anatomy
  _run_step

  # Ownership is the skills-directory shape inside the checkout, not the
  # checkout itself: a link the operator pointed at some other file in the repo
  # is not something this step ever wrote, and gets the same refusal as a link
  # pointing outside the workbench.
  echo "notes" > "$FAKE_WORKBENCH/README.md"
  ln -s "$FAKE_WORKBENCH/README.md" "$HOME/.claude/skills/mynote"

  _run_step
  [ "$status" -eq 0 ]
  [ -L "$HOME/.claude/skills/mynote" ]
  [[ "$output" == *"$HOME/.claude/skills/mynote was not installed by the workbench"* ]]
}

@test "a skill installed from a worktree is pruned once its source is gone" {
  _make_skill anatomy
  _run_step_from_worktree
  [ "$(readlink "$HOME/.claude/skills/anatomy")" = "$TMPDIR/main/ai/skills/anatomy" ]

  rm -rf "$FAKE_WORKBENCH/ai/skills/anatomy"
  _run_step_from_worktree
  [ "$status" -eq 0 ]
  [ ! -L "$HOME/.claude/skills/anatomy" ]
  [ ! -L "$HOME/.agents/skills/anatomy" ]
  [[ "$output" != *"was not installed by the workbench"* ]]
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

@test "a skill that stops being agent-backed becomes a symlink again" {
  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"
  _run_step
  [ -d "$HOME/.agents/skills/reviewer" ]
  [ ! -L "$HOME/.agents/skills/reviewer" ]

  _make_skill reviewer
  _run_step
  [ "$status" -eq 0 ]
  [ -L "$HOME/.agents/skills/reviewer" ]
  [ "$(readlink "$HOME/.agents/skills/reviewer")" = "$FAKE_WORKBENCH/ai/skills/reviewer" ]
  ! grep -q "REVIEW PROTOCOL BODY" "$HOME/.agents/skills/reviewer/SKILL.md"
}

@test "a skill directory with no SKILL.md is skipped, not fatal" {
  _make_skill anatomy
  mkdir -p "$FAKE_WORKBENCH/ai/skills/halfwritten"

  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.claude/skills/halfwritten" ]
  [ -L "$HOME/.claude/skills/anatomy" ]
  [ -L "$HOME/.agents/skills/anatomy" ]
}

@test "an override directory with no SKILL.md is skipped, not fatal" {
  _make_skill anatomy
  _make_skill machine
  mkdir -p "$TMPDIR/config/overrides/ai/skills/machine"

  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.claude/skills/machine" ]
  [ -L "$HOME/.claude/skills/anatomy" ]
  [ -L "$HOME/.agents/skills/anatomy" ]
}

@test "a hand-written skill the workbench never installed survives a prune" {
  _make_skill anatomy
  _run_step

  mkdir -p "$HOME/.claude/skills/mine" "$HOME/.agents/skills/mine"
  echo "mine" > "$HOME/.claude/skills/mine/SKILL.md"
  echo "mine" > "$HOME/.agents/skills/mine/SKILL.md"

  _run_step
  [ "$status" -eq 0 ]
  [ "$(cat "$HOME/.claude/skills/mine/SKILL.md")" = "mine" ]
  [ "$(cat "$HOME/.agents/skills/mine/SKILL.md")" = "mine" ]
}

@test "a removed agent-backed skill is still pruned from the Pi root" {
  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"
  _run_step
  [ -d "$HOME/.agents/skills/reviewer" ]

  rm -rf "$FAKE_WORKBENCH/ai/skills/reviewer"
  _run_step
  [ ! -e "$HOME/.agents/skills/reviewer" ]
}

@test "a spliced skill is stamped with the workbench's ownership marker" {
  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"

  _run_step
  [ -f "$HOME/.agents/skills/reviewer/.installed-by-otto-workbench" ]
}

@test "a real removal failure during a reinstall is reported, not swallowed as hand-written" {
  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"
  _run_step
  [ -d "$HOME/.agents/skills/reviewer" ]

  # Strips write permission on the ownership-marked target itself, so
  # _clear_skill_entry's rm -rf fails for a real reason (permissions) rather
  # than refusing because the marker is absent — the two outcomes this test
  # exists to keep distinguishable.
  chmod 555 "$HOME/.agents/skills/reviewer"

  _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *"Could not install reviewer"* ]]
  [[ "$output" != *"was not installed by the workbench"* ]]
  # The previous good install must survive a failed removal — collapsing it
  # into the "hand-written, leave it alone" path would skip the reinstall in
  # silence instead of surfacing the failure.
  [ -d "$HOME/.agents/skills/reviewer" ]
  grep -q "REVIEW PROTOCOL BODY" "$HOME/.agents/skills/reviewer/SKILL.md"

  chmod 755 "$HOME/.agents/skills/reviewer"
}

@test "an interrupted install still leaves the ownership marker behind" {
  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"

  _run_step_interrupted
  [ -d "$HOME/.agents/skills/reviewer" ]
  [ -f "$HOME/.agents/skills/reviewer/.installed-by-otto-workbench" ]
  [ ! -s "$HOME/.agents/skills/reviewer/SKILL.md" ]
}

@test "the next run repairs an install interrupted before its content" {
  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"
  _run_step_interrupted

  _run_step
  [ "$status" -eq 0 ]
  grep -q "REVIEW PROTOCOL BODY" "$HOME/.agents/skills/reviewer/SKILL.md"
  [[ "$output" != *"was not installed by the workbench"* ]]
}

@test "an agent file with no frontmatter is skipped rather than spliced empty" {
  _make_skill reviewer reviewer
  printf 'JUST A BODY\n' > "$FAKE_WORKBENCH/ai/claude/agents/reviewer.md"

  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.agents/skills/reviewer" ]
}

@test "print_skills_summary reports a plain skill in both roots and an agent-backed one in Pi only" {
  _make_skill anatomy
  _make_skill reviewer reviewer
  _make_agent reviewer "REVIEW PROTOCOL BODY"

  _run_summary
  [ "$status" -eq 0 ]

  local claude_section pi_section
  claude_section="$(printf '%s\n' "$output" | awk '/Claude Code/{f=1} f && /^  Pi /{exit} f')"
  pi_section="$(printf '%s\n' "$output" | awk '/^  Pi /{f=1} f')"

  [[ "$claude_section" == *"anatomy"* ]]
  [[ "$claude_section" != *"reviewer"* ]]
  [[ "$pi_section" == *"anatomy"* ]]
  [[ "$pi_section" == *"reviewer"* ]]
}
