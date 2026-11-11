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

@test "filesArgv pages a patch-free field list" {
  _js 'm.filesArgv("acme/box", 7)'
  [ "$status" -eq 0 ]
  [[ "$output" == *'"/repos/acme/box/pulls/7/files?per_page=100"'* ]]
  [[ "$output" == *'"--paginate"'* ]]
  [[ "$output" == *'"--jq"'* ]]
  [[ "$output" == *'has_patch'* ]]
  [[ "$output" != *'"--slurp"'* ]]
}

@test "filesArgv's filter drops patches and keeps has_patch" {
  filter=$(node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    const a = m.filesArgv('o/n', 7); process.stdout.write(a[a.indexOf('--jq') + 1]);
  ")
  run jq -c "$filter" <<<'[{"filename":"a.js","status":"added","additions":1,"deletions":0,"patch":"@@"},{"filename":"b.bin","status":"modified"}]'
  [ "$status" -eq 0 ]
  [[ "$output" != *'"patch":'* ]]
  [[ "$output" == *'"filename":"a.js"'*'"has_patch":true'* ]]
  [[ "$output" == *'"filename":"b.bin"'*'"has_patch":false'* ]]
}

@test "patchArgv reads one page and selects one file" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    const a = m.patchArgv('acme/box', 7, 3, process.argv[1]);
    const want = '.[] | select(.filename == ' + JSON.stringify(process.argv[1]) + ') | .patch // empty';
    process.stdout.write(String(a[1].includes('files?per_page=100&page=3') && a[3] === want));
  " -- $'a "b".js'
  [ "$status" -eq 0 ]
  [ "$output" = "true" ]
}

@test "patchArgv's filter runs in jq" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    const a = m.patchArgv('o/n', 1, 1, 'src/x.js');
    process.stdout.write(a[a.indexOf('--jq') + 1]);
  "
  [ "$status" -eq 0 ]
  filter="$output"
  run jq -r "$filter" <<<'[{"filename":"src/y.js","patch":"no"},{"filename":"src/x.js","patch":"@@ -1 +1 @@"}]'
  [ "$status" -eq 0 ]
  [ "$output" = '@@ -1 +1 @@' ]
  run jq -r "$filter" <<<'[{"filename":"src/x.js","patch":null}]'
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "graphqlArgv drops non-integer numbers and aliases each number" {
  _js 'm.graphqlArgv("acme/box", [12, "x", 3.5, 7])'
  [ "$status" -eq 0 ]
  [[ "$output" == *'pr0: pullRequest(number: 12)'* ]]
  [[ "$output" == *'pr1: pullRequest(number: 7)'* ]]
  [[ "$output" != *'number: 3.5'* ]]
  [[ "$output" != *'number: x'* ]]
}

@test "batches boundaries 0, 1, 50, 51" {
  _js 'm.batches([])'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
  _js 'm.batches([1])'
  [ "$output" = '[[1]]' ]
  _js 'm.batches(Array.from({length:50},(_,i)=>i))[0].length'
  [ "$output" = '50' ]
  _js 'm.batches(Array.from({length:50},(_,i)=>i)).length'
  [ "$output" = '1' ]
  _js 'm.batches(Array.from({length:51},(_,i)=>i)).map(b=>b.length)'
  [ "$output" = '[50,1]' ]
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

@test "mapGraphql ciStatus missing or other is none; a draft keeps its real CI" {
  _mapg '{"commits":{"nodes":[{"commit":{"statusCheckRollup":null}}]}}'
  [[ "$output" == *'"ciStatus":"none"'* ]]
  _mapg '{"commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":"WEIRD"}}}]}}'
  [[ "$output" == *'"ciStatus":"none"'* ]]
  _mapg '{"isDraft":true,"commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":"FAILURE"}}}]}}'
  [[ "$output" == *'"ciStatus":"failing"'* ]]
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
