#!/usr/bin/env bats
# The workbench displaces three upstream Superpowers skills by name collision:
# a skill in ai/skills/ named `using-git-worktrees` wins over the package's own,
# and the upstream copy is dropped.
#
# That is not a workbench behaviour — it is Pi's resolver, and the package entry
# in ai/pi/settings.json is written on the strength of it. A package-level
# `skills: ["!..."]` filter would be the obvious mechanism instead, but the
# Superpowers extension re-adds its whole skills directory through
# resources_discover, so a filtered skill comes back. Name collision is the only
# containment that survives, which leaves the resolver's precedence as the one
# assumption holding the shims in place.
#
# So this asks Pi's own loadSkills, not a reimplementation of it: if a Pi upgrade
# ever ranks a package resource ahead of ~/.agents/skills, the shims stop winning
# and every session silently gets upstream's `git worktree add` into .worktrees/
# and its three-option completion menu back. Nothing else in the suite would
# notice.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  AGENT_SKILLS="$TMPDIR/agent/skills"
  PKG_SKILLS="$TMPDIR/pkg/skills"
  mkdir -p "$AGENT_SKILLS" "$PKG_SKILLS" "$TMPDIR/cwd"

  SKILLS_JS="$(npm root -g 2>/dev/null)/@earendil-works/pi-coding-agent/dist/core/skills.js"
}

# _require_resolver — skips a test that has to ask Pi how it resolves names.
#
# Called per-test rather than from setup: the last test reads only this repo's
# own files, and gating it on pi would mean a runner without pi silently skips
# the one check that does not need it.
_require_resolver() {
  [[ -f "$SKILLS_JS" ]] || bats_skip "pi not installed — nothing to ask about resolution order"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _skill DIR NAME BODY — a loadable skill named NAME under DIR.
_skill() {
  local dir="$1/$2"
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" << MD
---
name: $2
description: "$3"
---

# $2

$3
MD
}

# _resolve — prints "<name> <WORKBENCH|upstream>" per skill, then a "collisions
# <n>" line. The package path is passed as a skillPaths entry, which is how Pi
# takes a package's skills directory.
_resolve() {
  cat > "$TMPDIR/probe.mjs" << JS
import { loadSkills } from "file://$SKILLS_JS";
const res = loadSkills({
  agentDir: "$TMPDIR/agent",
  cwd: "$TMPDIR/cwd",
  includeDefaults: true,
  skillPaths: ["$PKG_SKILLS"],
});
const workbench = (p) => p.startsWith("$AGENT_SKILLS");
for (const s of res.skills.sort((a, b) => a.name.localeCompare(b.name))) {
  console.log(s.name, workbench(s.filePath) ? "WORKBENCH" : "upstream");
}
const c = (res.diagnostics ?? []).filter((d) => d.type === "collision");
console.log("collisions", c.length);
JS
  node "$TMPDIR/probe.mjs"
}

@test "a workbench skill displaces a package skill of the same name" {
  _require_resolver
  _skill "$AGENT_SKILLS" using-git-worktrees "wt switch -c, not git worktree add"
  _skill "$PKG_SKILLS" using-git-worktrees "git worktree add into .worktrees/"

  run _resolve
  [ "$status" -eq 0 ]
  [[ "$output" == *"using-git-worktrees WORKBENCH"* ]]
  [[ "$output" == *"collisions 1"* ]]
}

@test "the displaced package skill is dropped, not merged or duplicated" {
  _require_resolver
  # One name resolves to exactly one file. A second copy left loadable would
  # mean the model could still reach upstream's procedure by name.
  _skill "$AGENT_SKILLS" finishing-a-development-branch "task pr:create --draft"
  _skill "$PKG_SKILLS" finishing-a-development-branch "three-option completion menu"

  run _resolve
  [ "$status" -eq 0 ]
  [ "$(grep -c '^finishing-a-development-branch ' <<< "$output")" = "1" ]
  [[ "$output" == *"finishing-a-development-branch WORKBENCH"* ]]
}

@test "a package skill the workbench does not override is still loaded" {
  _require_resolver
  # The override must displace the named skill and nothing else — the package
  # is adopted for the 12 skills it brings that the workbench has no answer to.
  _skill "$AGENT_SKILLS" using-git-worktrees "wt switch -c"
  _skill "$PKG_SKILLS" using-git-worktrees "git worktree add"
  _skill "$PKG_SKILLS" brainstorming "upstream-only skill"

  run _resolve
  [ "$status" -eq 0 ]
  [[ "$output" == *"brainstorming upstream"* ]]
  [[ "$output" == *"using-git-worktrees WORKBENCH"* ]]
}
