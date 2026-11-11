#!/usr/bin/env bats
# Tests for herdr-app PR issue badges independent of lane.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# Open empty PR fields; overlay JSON in _issues / callers.
_EMPTY_PR="state: 'OPEN', isDraft: false, inMergeQueue: false,
      mergeable: 'MERGEABLE', ciStatus: 'none', reviewDecision: null,
      myReviewState: null, isRequestedReviewer: false,
      hasNewChangesSinceReview: false, reviewing: false,
      author: 'alice', reviewRuns: []"

# Overlay JSON on an open, empty PR; print [kind, status, label] for each issue.
_issues() {
  run node --input-type=module -e "
    const { prIssues } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const pr = Object.assign({
      ${_EMPTY_PR}
    }, JSON.parse(process.argv[1]));
    process.stdout.write(JSON.stringify(prIssues(pr).map(i=>[i.kind,i.status,i.label])));
  " -- "$1"
}

@test "prIssues: clean PR has none" {
  _issues '{}'
  [ "$status" -eq 0 ]
  [ "$output" = "[]" ]
}

@test "prIssues: conflicts, failing CI, owed feedback, changes requested in order" {
  _issues '{"mergeable":"CONFLICTING","ciStatus":"failing","owedFeedback":"2 unresolved threads","reviewDecision":"CHANGES_REQUESTED"}'
  [ "$status" -eq 0 ]
  [ "$output" = '[["conflicts","fail","Conflicts"],["ci-failing","fail","CI failing"],["has-comments","warn","2 unresolved threads"],["changes-requested","warn","Changes requested"]]' ]
}

@test "prIssues: pending CI is ci-running" {
  _issues '{"ciStatus":"pending"}'
  [ "$status" -eq 0 ]
  [ "$output" = '[["ci-running","running","CI running"]]' ]
}

@test "prIssues: ciUnknown with none is ci-unknown; failing CI takes the one CI slot" {
  _issues '{"ciUnknown":true,"ciStatus":"none"}'
  [ "$status" -eq 0 ]
  [ "$output" = '[["ci-unknown","none","CI unknown"]]' ]
  _issues '{"ciUnknown":true,"ciStatus":"failing"}'
  [ "$status" -eq 0 ]
  [ "$output" = '[["ci-failing","fail","CI failing"]]' ]
}

@test "prIssues: owed feedback label is the first clause" {
  _issues '{"owedFeedback":"2 unresolved threads, 1 new comment, 3 drafts"}'
  [ "$status" -eq 0 ]
  [ "$output" = '[["has-comments","warn","2 unresolved threads"]]' ]
}

@test "prIssues: MERGED with conflicts is empty" {
  _issues '{"state":"MERGED","mergeable":"CONFLICTING"}'
  [ "$status" -eq 0 ]
  [ "$output" = "[]" ]
}

@test "prIssues: hydrated false is CI loading plus owed" {
  _issues '{"hydrated":false,"owedFeedback":"2 unresolved threads","ciStatus":"failing"}'
  [ "$status" -eq 0 ]
  [ "$output" = '[["ci-pending","none","CI loading"],["has-comments","warn","2 unresolved threads"]]' ]
}

@test "prIssues: hydrated absent still reports failing CI" {
  _issues '{"ciStatus":"failing"}'
  [ "$status" -eq 0 ]
  [ "$output" = '[["ci-failing","fail","CI failing"]]' ]
}

# passes-at-base: un-hydrated rows need no mineColumn change; pins the CLI contract
@test "mineColumn: hydrated false stays awaiting-review" {
  run node --input-type=module -e "
    const { mineColumn } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const pr = Object.assign({
      ${_EMPTY_PR}
    }, {hydrated: false});
    process.stdout.write(mineColumn(pr));
  "
  [ "$status" -eq 0 ]
  [ "$output" = "awaiting-review" ]
}

@test "prIssues: conflicting reviewing PR still lists conflicts; mineColumn is in-review" {
  _issues '{"mergeable":"CONFLICTING","reviewing":true}'
  [ "$status" -eq 0 ]
  [ "$output" = '[["conflicts","fail","Conflicts"]]' ]
  run node --input-type=module -e "
    const { mineColumn } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const pr = Object.assign({
      ${_EMPTY_PR}
    }, JSON.parse(process.argv[1]));
    process.stdout.write(mineColumn(pr));
  " -- '{"mergeable":"CONFLICTING","reviewing":true}'
  [ "$status" -eq 0 ]
  [ "$output" = "in-review" ]
}
