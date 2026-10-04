#!/usr/bin/env bats
# Tests for validate-skills — the Superpowers shims, lifecycle cadence hooks, and skill script paths.
setup() {
  load 'test_helper'
  common_setup
  load 'validate_skills_helper'
  # shellcheck disable=SC2034  # read by _run_validate in validate_skills_helper.bash
  VALIDATE_SKILLS="$REPO_ROOT/bin/local/validate-skills"

  FAKE_WORKBENCH="$TMPDIR/workbench"
  mkdir -p "$FAKE_WORKBENCH/ai/skills"
}

teardown() {
  common_teardown
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
#
# Matches on the literal trailing comma _make_shim always writes after the
# name (`Overrides superpowers:$name,`). If _make_shim's wording ever drops
# that comma, this sed finds no match and silently no-ops instead of failing
# loudly — keep the two in sync if either is touched.
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

# ── pr create invocations held against git-operations.md ─────────────────────

_write_git_ops_rule() {
  mkdir -p "$FAKE_WORKBENCH/ai/guidelines/rules"
  printf '%s\n' '- Always use `pr create --draft` to create a PR' \
    '- To close an issue on merge: `pr create --draft --closes 941`' \
    > "$FAKE_WORKBENCH/ai/guidelines/rules/git-operations.md"
}

@test "a skill teaching a pr create invocation the rule file gives passes" {
  _write_git_ops_rule
  _make_skill "ship"
  echo 'Run `pr create --draft --closes 941`.' >> "$FAKE_WORKBENCH/ai/skills/ship/SKILL.md"
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" == *"skills/ship: pr create invocation matches git-operations.md"* ]]
}

@test "a skill teaching a pr create invocation the rule file does not give fails" {
  # `pr create --draft` is a prefix of a canonical line; whole-line matching is
  # what keeps a skill that dropped `--closes 941` from passing as contained.
  _write_git_ops_rule
  _make_skill "ship"
  echo 'Run `pr create --draft --issue ENG-1`.' >> "$FAKE_WORKBENCH/ai/skills/ship/SKILL.md"
  _run_validate
  [ "$status" -eq 1 ]
  [[ "$output" == *'teaches `pr create --draft --issue ENG-1`'* ]]
}

@test "gh pr create in a skill is not read as the pr create invocation" {
  _write_git_ops_rule
  _make_skill "ship"
  echo 'Never `gh pr create --fill`.' >> "$FAKE_WORKBENCH/ai/skills/ship/SKILL.md"
  _run_validate
  [ "$status" -eq 0 ]
  [[ "$output" != *"teaches"* ]]
}
