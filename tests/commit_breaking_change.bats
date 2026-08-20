#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  source_lib
  # shellcheck disable=SC2034  # read by the commit-msg helpers in lib/ai/commit.sh
  COMMITLINT_CONFIG=""
  # Production always calls this before generate_commit_msg (Taskfile.global.yml)
  # — call it here too so COMMIT_RULES reflects the real prompt shape the
  # wiring tests below inspect.
  build_commit_rules
}

@test "conventions.sh defines BREAKING_CHANGE_FOOTER" {
  [[ "$BREAKING_CHANGE_FOOTER" == "BREAKING CHANGE" ]]
}

@test "conventions.sh defines NOT_BREAKING_FOOTER" {
  [[ "$NOT_BREAKING_FOOTER" == "Not-Breaking" ]]
}

@test "conventions.sh derives the hyphenated footer synonym" {
  [[ "$BREAKING_CHANGE_FOOTER_ALT" == "BREAKING-CHANGE" ]]
}

# lib/ai/core.sh sources conventions.sh from sh on the go-task path, and CI's
# /bin/sh is dash — a bashism in the footer helpers would only surface there.
@test "conventions.sh sources under a POSIX shell" {
  run sh -c ". '$REPO_ROOT/lib/conventions.sh' && printf '%s' \"\$BREAKING_CHANGE_FOOTER_ALT\""
  [ "$status" -eq 0 ]
  [[ "$output" == "BREAKING-CHANGE" ]]
}

# ── has_breaking_footer ────────────────────────────────────────────────────

@test "has_breaking_footer finds a BREAKING CHANGE footer in the body" {
  run has_breaking_footer "feat!: drop the legacy flag

BREAKING CHANGE: --legacy is gone; use --modern"
  [ "$status" -eq 0 ]
}

@test "has_breaking_footer accepts the hyphenated spelling" {
  run has_breaking_footer "feat!: drop the legacy flag

BREAKING-CHANGE: --legacy is gone; use --modern"
  [ "$status" -eq 0 ]
}

@test "has_breaking_footer rejects a footer with no reason after it" {
  run has_breaking_footer "feat!: drop the legacy flag

BREAKING CHANGE:"
  [ "$status" -eq 1 ]
}

@test "has_breaking_footer ignores the header's bang marker" {
  run has_breaking_footer "feat!: drop the legacy flag"
  [ "$status" -eq 1 ]
}

# ── declared_footers ───────────────────────────────────────────────────────

@test "declared_footers prints both footer kinds in order" {
  run declared_footers "refactor: rename the entry point

Not-Breaking: command:beta — renamed, alias kept
BREAKING CHANGE: config:old.key no longer read"
  [ "$status" -eq 0 ]
  [[ "${lines[0]}" == "Not-Breaking: command:beta — renamed, alias kept" ]]
  [[ "${lines[1]}" == "BREAKING CHANGE: config:old.key no longer read" ]]
}

@test "declared_footers prints nothing when the message declares nothing" {
  run declared_footers "fix: tighten the retry window"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

# ── validate_commit_msg — the bang marker needs its footer ─────────────────

@test "validate_commit_msg accepts a bang marker backed by a footer" {
  run validate_commit_msg "feat!: drop the legacy flag

BREAKING CHANGE: --legacy is gone; use --modern"
  [ "$status" -eq 0 ]
}

@test "validate_commit_msg accepts a bang marker with a scope and a footer" {
  run validate_commit_msg "feat(pr)!: rename --post to --publish

BREAKING CHANGE: --post no longer exists; use --publish"
  [ "$status" -eq 0 ]
}

# The pre-push gate (bin/local/check-surface-compat) fails this message minutes
# later — accepting it here only means discovering the same mistake twice.
@test "validate_commit_msg rejects a bang marker with no footer" {
  run validate_commit_msg "feat!: drop the legacy flag"
  [ "$status" -eq 1 ]
  [[ "$output" == *"$BREAKING_CHANGE_FOOTER"* ]]
}

@test "validate_commit_msg rejects a scoped bang marker with no footer" {
  run validate_commit_msg "feat(pr)!: rename --post to --publish"
  [ "$status" -eq 1 ]
}

@test "validate_commit_msg does not demand a footer without a bang marker" {
  run validate_commit_msg "feat: add the modern flag"
  [ "$status" -eq 0 ]
}

@test "validate_commit_msg still rejects a non-conventional header" {
  run validate_commit_msg "just some words"
  [ "$status" -eq 1 ]
}

@test "validate_commit_msg still rejects an unknown type with a bang" {
  run validate_commit_msg "wibble!: not a real type"
  [ "$status" -eq 1 ]
}

# ── _surface_removals ──────────────────────────────────────────────────────

@test "_surface_removals is empty when the gate reports nothing" {
  local fake="$BATS_TEST_TMPDIR/check-surface-compat"
  printf '#!/bin/bash\nexit 0\n' > "$fake"
  chmod +x "$fake"
  WORKBENCH_SURFACE_GATE="$fake"
  run --separate-stderr _surface_removals "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  # A genuinely clean gate produces no stderr either — unlike the "aborted
  # mid-run" case below, which looks identical on stdout alone (status 0,
  # empty output) but always leaves a stderr trace.
  [ -z "$stderr" ]
}

@test "_surface_removals parses REMOVED lines from the gate" {
  local fake="$BATS_TEST_TMPDIR/check-surface-compat"
  mkdir -p "$(dirname "$fake")"
  printf '#!/bin/bash\necho "REMOVED command:beta"\necho "REMOVED config:old.key"\nexit 1\n' > "$fake"
  chmod +x "$fake"
  WORKBENCH_SURFACE_GATE="$fake"
  run --separate-stderr _surface_removals "$REPO_ROOT"
  [[ "$output" == *"command:beta"* ]]
  [[ "$output" == *"config:old.key"* ]]
  [[ "$output" != *"REMOVED"* ]]
}

@test "_surface_removals discards a partial REMOVED list when the gate aborts mid-run" {
  local fake="$BATS_TEST_TMPDIR/check-surface-compat"
  # exit 128 is a git failure (e.g. no merge base) — the gate aborted before it
  # could tell us whether anything was removed. Any REMOVED lines printed
  # before the abort are a partial result and must not be trusted, but the
  # abort itself must not vanish without a trace either.
  printf '#!/bin/bash\necho "REMOVED command:beta"\nexit 128\n' > "$fake"
  chmod +x "$fake"
  WORKBENCH_SURFACE_GATE="$fake"
  run --separate-stderr _surface_removals "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [[ "$stderr" == *"exited 128"* ]]
}

@test "_surface_removals is silent when the gate path is not executable" {
  WORKBENCH_SURFACE_GATE="$BATS_TEST_TMPDIR/does-not-exist"
  run --separate-stderr _surface_removals "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

# ── _build_commit_prompt wiring ────────────────────────────────────────────

@test "_build_commit_prompt sends the removed-surface note to the AI when the gate flags removals" {
  local fake_gate="$BATS_TEST_TMPDIR/check-surface-compat"
  printf '#!/bin/bash\necho "REMOVED command:beta"\nexit 1\n' > "$fake_gate"
  chmod +x "$fake_gate"
  WORKBENCH_SURFACE_GATE="$fake_gate"

  mkdir -p "$BATS_TEST_TMPDIR/bin"
  printf '#!/bin/bash\ncat\n' > "$BATS_TEST_TMPDIR/bin/fake-ai"
  chmod +x "$BATS_TEST_TMPDIR/bin/fake-ai"
  AI_COMMAND="fake-ai"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  local removals
  removals=$(_surface_removals "$REPO_ROOT")
  _build_commit_prompt "some diff" "" "$removals"

  [[ "$AI_MSG" == *"command:beta"* ]]
  # $BREAKING_CHANGE_FOOTER alone is a weak assertion here: it's already in
  # COMMIT_RULES's fallback bullet regardless of the note. $NOT_BREAKING_FOOTER
  # only ever appears via surface_note, so it actually proves the note landed.
  [[ "$AI_MSG" == *"$NOT_BREAKING_FOOTER"* ]]

  # The note must sit next to the rules, before the diff — not orphaned after
  # it, which is the placement the brief specifically warned against.
  local before_diff="${AI_MSG%%Diff:*}"
  [[ "$before_diff" == *"command:beta"* ]]
}

@test "_build_commit_prompt lists removed entries without a bullet the model could copy into the footer" {
  local fake_gate="$BATS_TEST_TMPDIR/check-surface-compat"
  printf '#!/bin/bash\necho "REMOVED command:beta"\nexit 1\n' > "$fake_gate"
  chmod +x "$fake_gate"
  WORKBENCH_SURFACE_GATE="$fake_gate"

  mkdir -p "$BATS_TEST_TMPDIR/bin"
  printf '#!/bin/bash\ncat\n' > "$BATS_TEST_TMPDIR/bin/fake-ai"
  chmod +x "$BATS_TEST_TMPDIR/bin/fake-ai"
  AI_COMMAND="fake-ai"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  local removals
  removals=$(_surface_removals "$REPO_ROOT")
  _build_commit_prompt "some diff" "" "$removals"

  # A "- command:beta" bullet in the note risks the model literally copying
  # the dash into 'Not-Breaking: - command:beta — reason', which the gate's
  # _declared_keys (awk '{print $2}') reads as the entry "-", leaving
  # command:beta undeclared — the exact pre-push rejection this task exists
  # to remove. The entry must appear with no leading dash anywhere it's listed.
  [[ "$AI_MSG" == *"command:beta"* ]]
  [[ "$AI_MSG" != *"- command:beta"* ]]
}

@test "_build_commit_prompt sends no surface note when the gate is clean" {
  local fake_gate="$BATS_TEST_TMPDIR/check-surface-compat"
  printf '#!/bin/bash\nexit 0\n' > "$fake_gate"
  chmod +x "$fake_gate"
  # shellcheck disable=SC2034  # read by _surface_removals in lib/ai/commit.sh
  WORKBENCH_SURFACE_GATE="$fake_gate"

  mkdir -p "$BATS_TEST_TMPDIR/bin"
  printf '#!/bin/bash\ncat\n' > "$BATS_TEST_TMPDIR/bin/fake-ai"
  chmod +x "$BATS_TEST_TMPDIR/bin/fake-ai"
  # shellcheck disable=SC2034  # read by run_ai in lib/ai/core.sh
  AI_COMMAND="fake-ai"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  local removals
  removals=$(_surface_removals "$REPO_ROOT")
  _build_commit_prompt "some diff" "" "$removals"

  # "public surface" alone is a weak sentinel: COMMIT_RULES's fallback bullet
  # ("...anything on the public surface...") contains it independent of
  # surface_note. "removes the following entries" only ever comes from the note.
  [[ "$AI_MSG" != *"removes the following entries"* ]]
}

# ── preserve_declared_footers ──────────────────────────────────────────────
# task commit:reword regenerates the message from the commit's diff alone, so
# a footer the author already wrote is dropped unless it is carried across.

@test "preserve_declared_footers carries a Not-Breaking footer onto the new message" {
  AI_MSG="refactor(cli): rename the beta entry point"
  preserve_declared_footers "refactor(cli): rename beta

Not-Breaking: command:beta — renamed to command:release, alias kept for a cycle"

  [[ "$AI_MSG" == *"Not-Breaking: command:beta — renamed to command:release, alias kept for a cycle"* ]]
  [[ "$AI_MSG" == "refactor(cli): rename the beta entry point"* ]]
}

@test "preserve_declared_footers carries a BREAKING CHANGE footer onto the new message" {
  AI_MSG="feat(cli): drop the legacy flag"
  preserve_declared_footers "feat(cli)!: drop --legacy

BREAKING CHANGE: --legacy no longer exists; pass --modern instead"

  [[ "$AI_MSG" == *"BREAKING CHANGE: --legacy no longer exists; pass --modern instead"* ]]
}

# The reason text is the entire argument for a footer over a checked-in
# allowlist, so it has to reach git history byte for byte — em dash, wording
# and all. A model asked to preserve it would paraphrase.
@test "preserve_declared_footers reproduces the footer byte for byte" {
  local footer="Not-Breaking: config:review.phases.*.model — never read; the phase reads config:review.model"
  AI_MSG="refactor(review): drop the per-phase model key"
  preserve_declared_footers "refactor(review): drop per-phase model

$footer"

  run grep -qxF "$footer" <<<"$AI_MSG"
  [ "$status" -eq 0 ]
}

@test "preserve_declared_footers separates the carried footer with a blank line" {
  AI_MSG="feat(cli): drop the legacy flag"
  preserve_declared_footers "feat(cli)!: drop --legacy

BREAKING CHANGE: --legacy is gone"

  [[ "$AI_MSG" == "feat(cli): drop the legacy flag

BREAKING CHANGE: --legacy is gone" ]]
}

@test "preserve_declared_footers does not duplicate a footer the new message already has" {
  AI_MSG="feat(cli): drop the legacy flag

BREAKING CHANGE: --legacy is gone"
  preserve_declared_footers "feat(cli)!: drop --legacy

BREAKING CHANGE: --legacy is gone"

  local occurrences
  occurrences=$(grep -c '^BREAKING CHANGE: --legacy is gone$' <<<"$AI_MSG")
  [ "$occurrences" -eq 1 ]
}

@test "preserve_declared_footers replaces a regenerated footer for the same declaration" {
  AI_MSG="feat(cli)!: drop the legacy flag

BREAKING CHANGE: the legacy flag is removed"
  preserve_declared_footers "feat(cli)!: drop --legacy

BREAKING CHANGE: --legacy no longer exists; pass --modern instead"

  local occurrences
  occurrences=$(grep -c '^BREAKING CHANGE:' <<<"$AI_MSG")
  [ "$occurrences" -eq 1 ]
  [[ "$AI_MSG" == *"BREAKING CHANGE: --legacy no longer exists; pass --modern instead"* ]]
}

@test "preserve_declared_footers replaces a regenerated Not-Breaking footer for the same entry" {
  AI_MSG="refactor(cli): rename the beta entry point

Not-Breaking: command:beta — renamed"
  preserve_declared_footers "refactor(cli): rename beta

Not-Breaking: command:beta — renamed to command:release, alias kept for a cycle"

  local occurrences
  occurrences=$(grep -c '^Not-Breaking: command:beta' <<<"$AI_MSG")
  [ "$occurrences" -eq 1 ]
  [[ "$AI_MSG" == *"Not-Breaking: command:beta — renamed to command:release, alias kept for a cycle"* ]]
}

@test "preserve_declared_footers carries every footer the original declared" {
  AI_MSG="refactor(cli): tidy the entry points"
  preserve_declared_footers "refactor(cli): tidy entry points

Not-Breaking: command:beta — alias kept
Not-Breaking: command:gamma — alias kept"

  [[ "$AI_MSG" == *"Not-Breaking: command:beta — alias kept"* ]]
  [[ "$AI_MSG" == *"Not-Breaking: command:gamma — alias kept"* ]]
}

@test "preserve_declared_footers leaves a message with no footers to carry alone" {
  AI_MSG="fix(cli): tighten the retry window"
  preserve_declared_footers "fix(cli): retry sooner

Nothing declared here."

  [[ "$AI_MSG" == "fix(cli): tighten the retry window" ]]
}

# ── task commit:reword wiring ──────────────────────────────────────────────

@test "commit:reword reads the original message and preserves its footers" {
  local reword
  reword=$(awk '/^  commit:reword:/{f=1} /^  pr:content:/{f=0} f' "$REPO_ROOT/Taskfile.global.yml")
  [[ "$reword" == *'ORIGINAL_MSG=$(git log -1 --format=%B "$TARGET_SHA")'* ]]
  [[ "$reword" == *'preserve_declared_footers "$ORIGINAL_MSG"'* ]]

  # The carry-forward has to land before validation, or a regenerated `!`
  # header would be rejected for missing the footer that is about to be
  # re-appended to it.
  local before_validate="${reword%%validate_commit_msg*}"
  [[ "$before_validate" == *"preserve_declared_footers"* ]]
}
