#!/usr/bin/env bats
# Tests for herdr-app GitHub argv builders and JSON mappers.
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

_js() {
  run node --input-type=module -e "const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js'); process.stdout.write(JSON.stringify($1))"
}

@test "userArgv" {
  _js 'm.userArgv()'
  [ "$status" -eq 0 ]
  [ "$output" = '["api","/user","--jq",".login"]' ]
}

@test "pullsArgv pages with a --jq field list and no --slurp" {
  _js 'm.pullsArgv("acme/box")'
  [ "$status" -eq 0 ]
  [[ "$output" == *'"--method","GET"'* ]]
  [[ "$output" == *'"--paginate"'* ]]
  [[ "$output" == *'"/repos/acme/box/pulls"'* ]]
  [[ "$output" == *'"--jq"'* ]]
  [[ "$output" == *'html_url'* ]]
  [[ "$output" != *'"--slurp"'* ]]
}

@test "graphqlArgv drops non-integer numbers and aliases each number" {
  _js 'm.graphqlArgv("acme/box", [12, "x", 3.5, 7])'
  [ "$status" -eq 0 ]
  [[ "$output" == *'pr0: pullRequest(number: 12)'* ]]
  [[ "$output" == *'pr1: pullRequest(number: 7)'* ]]
  [[ "$output" != *'number: 3.5'* ]]
  [[ "$output" != *'number: x'* ]]
}

@test "batches boundaries 0, 1, 20, 21" {
  _js 'm.batches([])'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
  _js 'm.batches([1])'
  [ "$output" = '[[1]]' ]
  _js 'm.batches(Array.from({length:20},(_,i)=>i))[0].length'
  [ "$output" = '20' ]
  _js 'm.batches(Array.from({length:20},(_,i)=>i)).length'
  [ "$output" = '1' ]
  _js 'm.batches(Array.from({length:21},(_,i)=>i)).map(b=>b.length)'
  [ "$output" = '[20,1]' ]
}

@test "pulls --jq filter tolerates null labels" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    const a = m.pullsArgv('o/n'); process.stdout.write(a[a.indexOf('--jq')+1]);
  "
  [ "$status" -eq 0 ]
  run jq -c "$output" <<<'[{"number":1,"labels":null}]'
  [ "$status" -eq 0 ]
  [[ "$output" == *'"labels":[]'* ]]
}

_rest() {
  printf '%s' '{"number":3,"title":"t","user":{"login":"ada"},"head":{"ref":"feat","sha":"abc"},"base":{"ref":"main"},"draft":false,"html_url":"https://ex/3","labels":[{"name":"bug"}],"created_at":"c","updated_at":"u"}'
}

@test "mapPulls paged, flat, empty" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    const rest = JSON.parse(process.argv[1]);
    const want = {number:3,title:'t',author:'ada',branch:'feat',baseBranch:'main',draft:false,url:'https://ex/3',headSha:'abc',labels:['bug'],createdAt:'c',updatedAt:'u'};
    const paged = m.mapPulls([[rest],[rest]]);
    const flat = m.mapPulls([rest]);
    const empty = m.mapPulls([]);
    process.stdout.write(JSON.stringify({paged, flat, empty, ok: JSON.stringify(flat[0])===JSON.stringify(want) && paged.length===2 && empty.length===0}));
  " -- "$(_rest)"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ok":true'* ]]
}

_mapg() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    const overlay = JSON.parse(process.argv[1]);
    const numbers = JSON.parse(process.argv[2]);
    const pr = Object.assign({
      isDraft: false, reviewDecision: null, mergeable: 'MERGEABLE',
      additions: 0, deletions: 0, changedFiles: 0, mergeQueueEntry: null,
      commits: { nodes: [{ commit: { statusCheckRollup: { state: 'SUCCESS' } } }] },
      latestReviews: { nodes: [] }, reviewRequests: { nodes: [] },
      state: 'OPEN', mergedAt: null,
    }, overlay);
    process.stdout.write(JSON.stringify(m.mapGraphql({ data: { repository: { pr0: pr } } }, numbers)));
  " -- "$1" "${2:-[1]}"
}

@test "mapGraphql ciStatus SUCCESS is passing" {
  _mapg '{}'
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ciStatus":"passing"'* ]]
}

@test "mapGraphql ciStatus FAILURE and ERROR are failing" {
  _mapg '{"commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":"FAILURE"}}}]}}'
  [[ "$output" == *'"ciStatus":"failing"'* ]]
  _mapg '{"commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":"ERROR"}}}]}}'
  [[ "$output" == *'"ciStatus":"failing"'* ]]
}

@test "mapGraphql ciStatus PENDING and EXPECTED are pending" {
  _mapg '{"commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":"PENDING"}}}]}}'
  [[ "$output" == *'"ciStatus":"pending"'* ]]
  _mapg '{"commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":"EXPECTED"}}}]}}'
  [[ "$output" == *'"ciStatus":"pending"'* ]]
}

@test "mapGraphql ciStatus missing or other is none; draft forces none" {
  _mapg '{"commits":{"nodes":[{"commit":{"statusCheckRollup":null}}]}}'
  [[ "$output" == *'"ciStatus":"none"'* ]]
  _mapg '{"commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":"WEIRD"}}}]}}'
  [[ "$output" == *'"ciStatus":"none"'* ]]
  _mapg '{"isDraft":true,"commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":"FAILURE"}}}]}}'
  [[ "$output" == *'"ciStatus":"none"'* ]]
}

@test "mapGraphql mergeable MERGEABLE CONFLICTING else UNKNOWN" {
  _mapg '{"mergeable":"MERGEABLE"}'
  [[ "$output" == *'"mergeable":"MERGEABLE"'* ]]
  _mapg '{"mergeable":"CONFLICTING"}'
  [[ "$output" == *'"mergeable":"CONFLICTING"'* ]]
  _mapg '{"mergeable":"FOO"}'
  [[ "$output" == *'"mergeable":"UNKNOWN"'* ]]
  _mapg '{"mergeable":null}'
  [[ "$output" == *'"mergeable":"UNKNOWN"'* ]]
}

@test "mapGraphql drops Team reviewers and keeps User" {
  _mapg '{"reviewRequests":{"nodes":[{"requestedReviewer":{"__typename":"Team","login":"devs"}},{"requestedReviewer":{"__typename":"User","login":"ada"}}]}}'
  [[ "$output" == *'"requestedReviewers":["ada"]'* ]]
}

@test "mapGraphql skips a null PR" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    process.stdout.write(JSON.stringify(m.mapGraphql({data:{repository:{pr0:null}}}, [9])));
  "
  [ "$output" = '{}' ]
}

@test "mapGraphql mergeQueueEntry sets inMergeQueue true" {
  _mapg '{"mergeQueueEntry":{"id":"MQE_1"}}'
  [[ "$output" == *'"inMergeQueue":true'* ]]
}

@test "mapGraphql maps partial data when errors are present" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    const pr = { isDraft: false, mergeable: 'MERGEABLE', additions: 1, deletions: 0,
      changedFiles: 1, mergeQueueEntry: null, commits: { nodes: [] },
      latestReviews: { nodes: [] }, reviewRequests: { nodes: [] } };
    const json = { data: { repository: { pr0: pr, pr1: null } }, errors: [{ message: 'x' }] };
    process.stdout.write(JSON.stringify(m.mapGraphql(json, [10, 11])));
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *'"10":'* ]]
  [[ "$output" != *'"11":'* ]]
}

@test "mapGraphql garbage input is {}" {
  _js 'm.mapGraphql(null, [1])'
  [ "$output" = '{}' ]
  _js 'm.mapGraphql("nope", [1])'
  [ "$output" = '{}' ]
  _js 'm.mapGraphql(1, [1])'
  [ "$output" = '{}' ]
  _js 'm.mapGraphql([], [1])'
  [ "$output" = '{}' ]
}
