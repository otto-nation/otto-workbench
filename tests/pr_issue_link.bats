#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  source_lib
  # Per-test scratch space. make_fake_provider plants an executable at
  # $TMPDIR/bin/python3, and the machine's real TMPDIR is shared by every test
  # running in parallel: two cases writing that one path truncate it out from
  # under a third that is executing it, so the stub reports the wrong provider.
  TMPDIR="$BATS_TEST_TMPDIR"
  parse_pr_flags ""
}

teardown() {
  common_teardown
}

# make_fake_provider VALUE — stub the config reader _pr_issue_provider shells
# out to, in the three-tab-field record format lib/config_cli.py prints.
# An empty VALUE stubs a repo where no scope sets issues.provider.
make_fake_provider() {
  local value="$1"
  mkdir -p "$TMPDIR/bin"
  cat > "$TMPDIR/bin/python3" << SCRIPT
#!/usr/bin/env bash
[ -n "$value" ] || exit 1
printf 'project\t%s\t/tmp/repo\n' "$value"
SCRIPT
  chmod +x "$TMPDIR/bin/python3"
  PATH="$TMPDIR/bin:$PATH"
}

@test "a numeric ref is appended to a templated body" {
  PR_DESCRIPTION="## What

Adds a thing.

## Why

It was missing."
  parse_pr_flags "--closes 941"
  _pr_append_issue_link
  [[ "$PR_DESCRIPTION" == *"Closes #941"* ]]
}

@test "the ref lands after the last template section, not before the first" {
  PR_DESCRIPTION="## What

Adds a thing."
  parse_pr_flags "--closes 941"
  _pr_append_issue_link
  [[ "$PR_DESCRIPTION" == "## What"* ]]
  [[ "$PR_DESCRIPTION" == *"Closes #941" ]]
}

@test "the ref is separated from the body by exactly one blank line" {
  PR_DESCRIPTION="## What"
  parse_pr_flags "--closes 941"
  _pr_append_issue_link
  [ "$PR_DESCRIPTION" = "## What

Closes #941" ]
}

@test "a body ending in newlines gains no extra blank lines" {
  PR_DESCRIPTION="## What

"
  parse_pr_flags "--closes 941"
  _pr_append_issue_link
  [ "$PR_DESCRIPTION" = "## What

Closes #941" ]
}

@test "two refs produce two lines" {
  PR_DESCRIPTION="body"
  parse_pr_flags "--closes 941 --closes 942"
  _pr_append_issue_link
  [[ "$PR_DESCRIPTION" == *"Closes #941"* ]]
  [[ "$PR_DESCRIPTION" == *"Closes #942"* ]]
}

@test "a second pass over the same body adds nothing" {
  PR_DESCRIPTION="body"
  parse_pr_flags "--closes 941"
  _pr_append_issue_link
  local once="$PR_DESCRIPTION"
  _pr_append_issue_link
  [ "$PR_DESCRIPTION" = "$once" ]
}

@test "a ref the body already closes under another keyword is not duplicated" {
  PR_DESCRIPTION="body

Fixes #941"
  parse_pr_flags "--closes 941"
  _pr_append_issue_link
  [ "$(printf '%s' "$PR_DESCRIPTION" | grep -ciE '(closes|fixes) #941')" -eq 1 ]
}

@test "#1 is added to a body that only closes #12" {
  PR_DESCRIPTION="body

Closes #12"
  parse_pr_flags "--closes 1"
  _pr_append_issue_link
  [[ "$PR_DESCRIPTION" == *"Closes #1" ]]
}

@test "no refs leaves the body untouched and returns zero" {
  PR_DESCRIPTION="body"
  _pr_append_issue_link
  [ "$?" -eq 0 ]
  [ "$PR_DESCRIPTION" = "body" ]
}

@test "a leading # on the flag value is normalised away" {
  PR_DESCRIPTION="body"
  parse_pr_flags "--closes #941"
  _pr_append_issue_link
  [[ "$PR_DESCRIPTION" == *"Closes #941" ]]
  [[ "$PR_DESCRIPTION" != *"##941"* ]]
}

@test "a tracker key is refused when the provider is github" {
  make_fake_provider "github"
  run parse_pr_flags "--closes ENG-123"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ENG-123"* ]]
  [[ "$output" == *"github"* ]]
}

@test "a tracker key is refused when no provider is set" {
  make_fake_provider ""
  run parse_pr_flags "--closes ENG-123"
  [ "$status" -eq 1 ]
  [[ "$output" == *"unset"* ]]
}

@test "a tracker key is accepted when the provider is linear" {
  make_fake_provider "linear"
  PR_DESCRIPTION="body"
  parse_pr_flags "--closes ENG-123"
  _pr_append_issue_link
  [[ "$PR_DESCRIPTION" == *"Closes ENG-123" ]]
}

@test "a numeric ref is accepted whatever the provider" {
  make_fake_provider "jira"
  PR_DESCRIPTION="body"
  parse_pr_flags "--closes 941"
  _pr_append_issue_link
  [[ "$PR_DESCRIPTION" == *"Closes #941" ]]
}

@test "an unparseable ref is refused" {
  run parse_pr_flags "--closes banana"
  [ "$status" -eq 1 ]
  [[ "$output" == *"banana"* ]]
}

@test "_pr_resolve_issue reads nothing from stdin when the branch has no key" {
  run _pr_resolve_issue "isaac/fix/no_key_here" < /dev/null
  [ "$status" -eq 0 ]
  [ -z "$PR_ISSUE" ]
}

@test "_pr_resolve_issue still scrapes a key from the branch name" {
  _pr_resolve_issue "isaac/ENG-123/thing" < /dev/null
  [ "$PR_ISSUE" = "ENG-123" ]
}

@test "pr_preserve_close_refs restores a ref the regenerated body dropped" {
  PR_DESCRIPTION="a fresh body"
  pr_preserve_close_refs "old body

Closes #941"
  [[ "$PR_DESCRIPTION" == *"Closes #941" ]]
}

@test "pr_preserve_close_refs normalises a Fixes keyword to Closes" {
  PR_DESCRIPTION="a fresh body"
  pr_preserve_close_refs "old body

Fixes #941"
  [[ "$PR_DESCRIPTION" == *"Closes #941" ]]
}

@test "pr_preserve_close_refs does not duplicate a ref the new body kept" {
  PR_DESCRIPTION="fresh body

Closes #941"
  pr_preserve_close_refs "old body

Closes #941"
  [ "$(printf '%s' "$PR_DESCRIPTION" | grep -c 'Closes #941')" -eq 1 ]
}

@test "pr_preserve_close_refs is a no-op on an empty old body" {
  PR_DESCRIPTION="fresh body"
  pr_preserve_close_refs ""
  [ "$PR_DESCRIPTION" = "fresh body" ]
}

@test "pr_preserve_close_refs is a no-op when the old body closed nothing" {
  PR_DESCRIPTION="fresh body"
  pr_preserve_close_refs "old body with no refs, and a bare #941 mention"
  [ "$PR_DESCRIPTION" = "fresh body" ]
}

@test "generate_pr_content links when both title and body are overridden" {
  # shellcheck disable=SC2034  # both read by generate_pr_content in the sourced lib
  PR_TITLE_OVERRIDE="feat: thing"
  # shellcheck disable=SC2034
  PR_BODY_OVERRIDE="a body"
  PR_CLOSES=("#941")
  generate_pr_content "isaac/fix/thing" "main"
  [[ "$PR_DESCRIPTION" == *"Closes #941" ]]
}

@test "a ref shaped to inject regex is refused" {
  run parse_pr_flags "--closes 'A.*-1'"
  [ "$status" -eq 1 ]
}

@test "a lowercase tracker key is refused" {
  make_fake_provider "linear"
  run parse_pr_flags "--closes eng-123"
  [ "$status" -eq 1 ]
}

@test "the same ref passed twice is staged once" {
  parse_pr_flags "--closes 941 --closes 941"
  [ "${#PR_CLOSES[@]}" -eq 1 ]
}

@test "two spellings of the same ref are staged once" {
  parse_pr_flags "--closes 941 --closes #941"
  [ "${#PR_CLOSES[@]}" -eq 1 ]
}

@test "a duplicated ref produces one line in the body" {
  PR_DESCRIPTION="body"
  parse_pr_flags "--closes 941 --closes 941"
  _pr_append_issue_link
  [ "$(printf '%s' "$PR_DESCRIPTION" | grep -c 'Closes #941')" -eq 1 ]
}

@test "distinct refs are still both staged" {
  parse_pr_flags "--closes 941 --closes 942"
  [ "${#PR_CLOSES[@]}" -eq 2 ]
}
