#!/usr/bin/env bats
# Tests for herdr-app team/mine PR column classification and list filters.
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

# Overlay JSON on an open, empty PR; print teamColumn.
_team() {
  run node --input-type=module -e "
    const { teamColumn } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const pr = Object.assign({
      state: 'OPEN', isDraft: false, inMergeQueue: false,
      mergeable: 'MERGEABLE', ciStatus: 'none', reviewDecision: null,
      myReviewState: null, isRequestedReviewer: false,
      hasNewChangesSinceReview: false, reviewing: false,
      author: 'alice', reviewRuns: [],
    }, JSON.parse(process.argv[1]));
    process.stdout.write(teamColumn(pr));
  " -- "$1"
}

# Overlay JSON; print mineColumn.
_mine() {
  run node --input-type=module -e "
    const { mineColumn } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const pr = Object.assign({
      state: 'OPEN', isDraft: false, inMergeQueue: false,
      mergeable: 'MERGEABLE', ciStatus: 'none', reviewDecision: null,
      myReviewState: null, isRequestedReviewer: false,
      hasNewChangesSinceReview: false, reviewing: false,
      author: 'alice', reviewRuns: [],
    }, JSON.parse(process.argv[1]));
    process.stdout.write(mineColumn(pr));
  " -- "$1"
}

# prs JSON, filters JSON, me; print JSON array of PR numbers.
_filter() {
  run node --input-type=module -e "
    const { filterPrs } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const prs = JSON.parse(process.argv[1]);
    const filters = JSON.parse(process.argv[2]);
    const me = process.argv[3];
    process.stdout.write(JSON.stringify(filterPrs(prs, filters, me).map((p) => p.number)));
  " -- "$1" "$2" "$3"
}

@test "teamColumn: reviewing beats merged" {
  _team '{"reviewing":true,"state":"MERGED"}'
  [ "$status" -eq 0 ]
  [ "$output" = "in-queue" ]
}

@test "teamColumn: merged -> landed" {
  _team '{"state":"MERGED"}'
  [ "$status" -eq 0 ]
  [ "$output" = "landed" ]
}

@test "teamColumn: inMergeQueue -> landed" {
  _team '{"inMergeQueue":true}'
  [ "$status" -eq 0 ]
  [ "$output" = "landed" ]
}

@test "teamColumn: merged beats isRequestedReviewer" {
  _team '{"state":"MERGED","isRequestedReviewer":true}'
  [ "$status" -eq 0 ]
  [ "$output" = "landed" ]
}

@test "teamColumn: isRequestedReviewer -> needs-my-review" {
  _team '{"isRequestedReviewer":true}'
  [ "$status" -eq 0 ]
  [ "$output" = "needs-my-review" ]
}

@test "teamColumn: hasNewChangesSinceReview -> needs-my-review" {
  _team '{"hasNewChangesSinceReview":true}'
  [ "$status" -eq 0 ]
  [ "$output" = "needs-my-review" ]
}

@test "teamColumn: needs-my-review beats an unposted run" {
  _team '{"isRequestedReviewer":true,"reviewRuns":[{"postedEvent":null}]}'
  [ "$status" -eq 0 ]
  [ "$output" = "needs-my-review" ]
}

@test "teamColumn: unposted newer than posted -> review-ready" {
  _team '{"reviewRuns":[{"postedEvent":null},{"postedEvent":"approve"}]}'
  [ "$status" -eq 0 ]
  [ "$output" = "review-ready" ]
}

@test "teamColumn: unposted older than newest posted -> reviewed" {
  _team '{"reviewRuns":[{"postedEvent":"approve"},{"postedEvent":null}]}'
  [ "$status" -eq 0 ]
  [ "$output" = "reviewed" ]
}

@test "teamColumn: only unposted run (never posted) -> review-ready" {
  _team '{"reviewRuns":[{"postedEvent":null}]}'
  [ "$status" -eq 0 ]
  [ "$output" = "review-ready" ]
}

@test "teamColumn: dismissed counts as posted -> reviewed" {
  _team '{"reviewRuns":[{"postedEvent":"dismissed"},{"postedEvent":null}]}'
  [ "$status" -eq 0 ]
  [ "$output" = "reviewed" ]
}

@test "teamColumn: myReviewState APPROVED without runs -> reviewed" {
  _team '{"myReviewState":"APPROVED"}'
  [ "$status" -eq 0 ]
  [ "$output" = "reviewed" ]
}

@test "teamColumn: runs all posted -> reviewed" {
  _team '{"reviewRuns":[{"postedEvent":"approve"},{"postedEvent":"comment"}]}'
  [ "$status" -eq 0 ]
  [ "$output" = "reviewed" ]
}

@test "teamColumn: nothing -> open" {
  _team '{}'
  [ "$status" -eq 0 ]
  [ "$output" = "open" ]
}

@test "mineColumn: merged" {
  _mine '{"state":"MERGED"}'
  [ "$status" -eq 0 ]
  [ "$output" = "merged" ]
}

@test "mineColumn: inMergeQueue -> in-queue" {
  _mine '{"inMergeQueue":true}'
  [ "$status" -eq 0 ]
  [ "$output" = "in-queue" ]
}

@test "mineColumn: merged beats inMergeQueue" {
  _mine '{"state":"MERGED","inMergeQueue":true}'
  [ "$status" -eq 0 ]
  [ "$output" = "merged" ]
}

@test "mineColumn: mergeable CONFLICTING -> has-conflicts" {
  _mine '{"mergeable":"CONFLICTING"}'
  [ "$status" -eq 0 ]
  [ "$output" = "has-conflicts" ]
}

@test "mineColumn: CONFLICTING beats ciStatus failing" {
  _mine '{"mergeable":"CONFLICTING","ciStatus":"failing"}'
  [ "$status" -eq 0 ]
  [ "$output" = "has-conflicts" ]
}

@test "mineColumn: ci failing -> ci-failing" {
  _mine '{"ciStatus":"failing"}'
  [ "$status" -eq 0 ]
  [ "$output" = "ci-failing" ]
}

@test "mineColumn: ci-failing beats CHANGES_REQUESTED" {
  _mine '{"ciStatus":"failing","reviewDecision":"CHANGES_REQUESTED"}'
  [ "$status" -eq 0 ]
  [ "$output" = "ci-failing" ]
}

@test "mineColumn: CHANGES_REQUESTED -> changes-requested" {
  _mine '{"reviewDecision":"CHANGES_REQUESTED"}'
  [ "$status" -eq 0 ]
  [ "$output" = "changes-requested" ]
}

@test "mineColumn: changes-requested beats isDraft" {
  _mine '{"reviewDecision":"CHANGES_REQUESTED","isDraft":true}'
  [ "$status" -eq 0 ]
  [ "$output" = "changes-requested" ]
}

@test "mineColumn: isDraft -> draft" {
  _mine '{"isDraft":true}'
  [ "$status" -eq 0 ]
  [ "$output" = "draft" ]
}

@test "mineColumn: APPROVED + ciStatus passing -> approved" {
  _mine '{"reviewDecision":"APPROVED","ciStatus":"passing"}'
  [ "$status" -eq 0 ]
  [ "$output" = "approved" ]
}

@test "mineColumn: APPROVED + none -> approved" {
  _mine '{"reviewDecision":"APPROVED","ciStatus":"none"}'
  [ "$status" -eq 0 ]
  [ "$output" = "approved" ]
}

@test "mineColumn: APPROVED + pending -> pending-review" {
  _mine '{"reviewDecision":"APPROVED","ciStatus":"pending"}'
  [ "$status" -eq 0 ]
  [ "$output" = "pending-review" ]
}

@test "mineColumn: default -> pending-review" {
  _mine '{}'
  [ "$status" -eq 0 ]
  [ "$output" = "pending-review" ]
}

@test "isMine: viewer wins over me; author vs me is case-insensitive" {
  run node --input-type=module -e "
    const { isMine } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    process.stdout.write(JSON.stringify({
      a: isMine({author:'Bob',viewer:'bob'}, 'alice'),
      b: isMine({author:'bob'}, 'BOB'),
    }));
  "
  [ "$status" -eq 0 ]
  jq -e '.a and .b' <<<"$output"
}

@test "filterPrs: the viewer is dropped from the authors filter, case-insensitively" {
  _filter '[{"number":1,"author":"Alice","isDraft":false,"state":"OPEN","reviewRuns":[]},{"number":2,"author":"bob","isDraft":false,"state":"OPEN","reviewRuns":[]}]' '{"authors":["alice"]}' 'ALICE'
  [ "$status" -eq 0 ]
  [ "$output" = "[1,2]" ]
}

@test "filterPrs: drafts hidden by default" {
  _filter '[{"number":1,"author":"alice","isDraft":true,"state":"OPEN","reviewRuns":[]},{"number":2,"author":"alice","isDraft":false,"state":"OPEN","reviewRuns":[]}]' '{}' 'alice'
  [ "$status" -eq 0 ]
  [ "$output" = "[2]" ]
}

@test "filterPrs: drafts shown with drafts:true" {
  _filter '[{"number":1,"author":"alice","isDraft":true,"state":"OPEN","reviewRuns":[]},{"number":2,"author":"alice","isDraft":false,"state":"OPEN","reviewRuns":[]}]' '{"drafts":true}' 'alice'
  [ "$status" -eq 0 ]
  [ "$output" = "[1,2]" ]
}

@test "filterPrs: landed column hidden by default" {
  _filter '[{"number":1,"author":"bob","isDraft":false,"state":"MERGED","reviewRuns":[]},{"number":2,"author":"bob","isDraft":false,"state":"OPEN","reviewRuns":[]}]' '{}' 'alice'
  [ "$status" -eq 0 ]
  [ "$output" = "[2]" ]
}

@test "filterPrs: landed shown with landed:true" {
  _filter '[{"number":1,"author":"bob","isDraft":false,"state":"MERGED","reviewRuns":[]},{"number":2,"author":"bob","isDraft":false,"state":"OPEN","reviewRuns":[]}]' '{"landed":true}' 'alice'
  [ "$status" -eq 0 ]
  [ "$output" = "[1,2]" ]
}

@test "filterPrs: authors filter is case-insensitive" {
  _filter '[{"number":1,"author":"Alice","isDraft":false,"state":"OPEN","reviewRuns":[]},{"number":2,"author":"Bob","isDraft":false,"state":"OPEN","reviewRuns":[]}]' '{"authors":["alice"]}' 'x'
  [ "$status" -eq 0 ]
  [ "$output" = "[1]" ]
}

@test "filterPrs: empty authors keeps all" {
  _filter '[{"number":1,"author":"alice","isDraft":false,"state":"OPEN","reviewRuns":[]},{"number":2,"author":"bob","isDraft":false,"state":"OPEN","reviewRuns":[]}]' '{"authors":[]}' 'x'
  [ "$status" -eq 0 ]
  [ "$output" = "[1,2]" ]
}

@test "filterPrs: a teammate author filter still narrows when the viewer is also listed" {
  _filter '[{"number":1,"author":"alice","isDraft":false,"state":"OPEN","reviewRuns":[]},{"number":2,"author":"bob","isDraft":false,"state":"OPEN","reviewRuns":[]}]' '{"authors":["alice","bob"]}' 'alice'
  [ "$status" -eq 0 ]
  [ "$output" = "[2]" ]
}

@test "visibleColumns: drops empty columns when showEmpty is false" {
  run node --input-type=module -e "
    const { visibleColumns } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const cols = [
      { key: 'open', items: [{}] },
      { key: 'landed', items: [] },
    ];
    process.stdout.write(JSON.stringify(visibleColumns(cols, false).map((c) => c.key)));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '["open"]' ]
}

@test "visibleColumns: keeps all columns when showEmpty is true" {
  run node --input-type=module -e "
    const { visibleColumns } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const cols = [
      { key: 'open', items: [{}] },
      { key: 'landed', items: [] },
    ];
    process.stdout.write(JSON.stringify(visibleColumns(cols, true).map((c) => c.key)));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '["open","landed"]' ]
}
