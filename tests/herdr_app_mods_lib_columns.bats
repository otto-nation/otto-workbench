#!/usr/bin/env bats
# Tests for herdr-app PR board list filters and document lanes.
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

_cols() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    process.stdout.write(JSON.stringify($1));
  "
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

@test "DEFAULT_FILTERS hides drafts, mine drafts, landed, and empty" {
  run node --input-type=module -e "
    const { DEFAULT_FILTERS } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    process.stdout.write(JSON.stringify(DEFAULT_FILTERS));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '{"drafts":false,"mineDrafts":false,"landed":false,"empty":false,"hideBots":false}' ]
}

@test "filterOn: unset takes DEFAULT_FILTERS; stored boolean wins; unknown key is false" {
  run node --input-type=module -e "
    const { filterOn } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    process.stdout.write(JSON.stringify({
      unsetDrafts: filterOn({}, 'drafts'),
      unsetMine: filterOn(null, 'mineDrafts'),
      storedTrue: filterOn({mineDrafts: true}, 'mineDrafts'),
      storedFalse: filterOn({drafts: false}, 'drafts'),
      unknown: filterOn({}, 'nope'),
    }));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '{"unsetDrafts":false,"unsetMine":false,"storedTrue":true,"storedFalse":false,"unknown":false}' ]
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
  _filter '[{"number":1,"author":"bob","isDraft":false,"state":"MERGED","lanes":{"team":"landed"},"reviewRuns":[]},{"number":2,"author":"bob","isDraft":false,"state":"OPEN","lanes":{"team":"open"},"reviewRuns":[]}]' '{}' 'alice'
  [ "$status" -eq 0 ]
  [ "$output" = "[2]" ]
}

@test "filterPrs: landed shown with landed:true" {
  _filter '[{"number":1,"author":"bob","isDraft":false,"state":"MERGED","lanes":{"team":"landed"},"reviewRuns":[]},{"number":2,"author":"bob","isDraft":false,"state":"OPEN","lanes":{"team":"open"},"reviewRuns":[]}]' '{"landed":true}' 'alice'
  [ "$status" -eq 0 ]
  [ "$output" = "[1,2]" ]
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

@test "filterRepos: keeps only selected repos case-insensitively" {
  run node --input-type=module -e "
    const { filterRepos } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const prs = [
      { number: 1, repoSlug: 'Owner/One' },
      { number: 2, repoSlug: 'owner/two' },
      { number: 3, repoSlug: 'owner/one' },
    ];
    process.stdout.write(JSON.stringify(filterRepos(prs, ['OWNER/ONE']).map((p) => p.number)));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[1,3]' ]
}

@test "filterRepos: ignores a selection naming no known repo" {
  run node --input-type=module -e "
    const { filterRepos } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const prs = [
      { number: 1, repoSlug: 'owner/one' },
      { number: 2, repoSlug: 'owner/two' },
    ];
    process.stdout.write(JSON.stringify(filterRepos(prs, ['missing/repo']).map((p) => p.number)));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[1,2]' ]
}

@test "filterRepos: empty selection returns all" {
  run node --input-type=module -e "
    const { filterRepos } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const prs = [
      { number: 1, repoSlug: 'owner/one' },
      { number: 2, repoSlug: 'owner/two' },
    ];
    process.stdout.write(JSON.stringify(filterRepos(prs, []).map((p) => p.number)));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[1,2]' ]
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

@test "filterRepos: a selected repo with no PRs shows nothing when it is a loaded repo" {
  run node --input-type=module -e "
    const { filterRepos } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const prs = [{ number: 1, repoSlug: 'a/b' }];
    process.stdout.write(JSON.stringify([
      filterRepos(prs, ['c/d'], ['a/b', 'c/d']).length,
      filterRepos(prs, ['x/y'], ['a/b', 'c/d']).length,
    ]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = "[0,1]" ]
}

@test "filterFacets repos first-seen order, labels sorted case-insensitively, deduped" {
  _cols 'm.filterFacets([{repoSlug:"b/two",labels:["Zebra","alpha"]},{repoSlug:"a/one",labels:["ALPHA","bug"]},{repoSlug:"b/two",labels:["gamma"]},{repoSlug:"B/Two",labels:["bug"]}])'
  [ "$status" -eq 0 ]
  [ "$output" = '{"repos":["b/two","a/one"],"labels":["alpha","bug","gamma","Zebra"]}' ]
}

@test "filterFacets empty list and missing labels" {
  _cols '[m.filterFacets([]),m.filterFacets(null),m.filterFacets([{repoSlug:"a/one"}])]' 
  [ "$status" -eq 0 ]
  [ "$output" = '[{"repos":[],"labels":[]},{"repos":[],"labels":[]},{"repos":["a/one"],"labels":[]}]' ]
}

@test "filterLabels: any selected label matches, case-insensitive; stale or empty selections are ignored" {
  _cols '(() => { const prs = [{n:1,labels:["Bug"]},{n:2,labels:["docs"]},{n:3,labels:[]},{n:4}]; const ids = (l) => m.filterLabels(prs, l).map((p) => p.n); return [ids([]),ids(undefined),ids(["bug"]),ids(["BUG","docs"]),ids(["gone"]),ids(["gone","docs"])]; })()'
  [ "$status" -eq 0 ]
  [ "$output" = '[[1,2,3,4],[1,2,3,4],[1],[1,2],[1,2,3,4],[2]]' ]
}
