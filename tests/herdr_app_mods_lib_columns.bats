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

@test "isMine: a boolean row isMine wins over the login comparison" {
  run node --input-type=module -e "
    const { isMine } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    process.stdout.write(JSON.stringify([
      isMine({author:'bob',isMine:false}, 'bob'),
      isMine({author:'carol',isMine:true}, 'bob'),
    ]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[false,true]' ]
}

@test "isMine: an empty viewer or author is never mine" {
  run node --input-type=module -e "
    const { isMine } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    process.stdout.write(JSON.stringify([
      isMine({author:''}, ''),
      isMine({}, undefined),
      isMine({author:'bob'}, ''),
      isMine({author:'', viewer:'bob'}, 'bob'),
      isMine(undefined, 'bob'),
    ]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[false,false,false,false,false]' ]
}

@test "filterPrs is retired: tabList owns board membership" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    process.stdout.write(String(typeof m.filterPrs));
  "
  [ "$status" -eq 0 ]
  [ "$output" = 'undefined' ]
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

@test "TEAM_COLUMNS and MINE_COLUMNS are board.columns' keys, labels and order" {
  _cols "[...m.TEAM_COLUMNS.map((x, i) => ['team', x.key, x.label, i]), ...m.MINE_COLUMNS.map((x, i) => ['mine', x.key, x.label, i])]"
  [ "$status" -eq 0 ]
  js="$output"
  run python3 -c "
import json, sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import board.columns
print(json.dumps([[col.board.value, col.key, col.label, col.order] for col in board.columns.columns()]))
"
  [ "$status" -eq 0 ]
  [ "$(jq -c . <<<"$js")" = "$(jq -c . <<<"$output")" ]
}

@test "laneOf: queue-lanes sends queued to In Queue and running to In Progress; Mine queued stays resting" {
  _cols '(() => {
    const lanes = { team: "open", mine: "awaiting-review", team_reviewing: "in-progress", mine_reviewing: "in-review" };
    const feat = ["queue-lanes"];
    const queued = { lanes, features: feat, queue: { state: "queued" } };
    const running = { lanes, features: feat, queue: { state: "running" } };
    const old = { lanes, queue: { state: "queued" } };
    const both = { lanes, features: feat, queue: { state: "queued" }, reviewing: true };
    return {
      queuedTeam: m.laneOf(queued, "team"), queuedMine: m.laneOf(queued, "mine"),
      runningTeam: m.laneOf(running, "team"), runningMine: m.laneOf(running, "mine"),
      oldTeam: m.laneOf(old, "team"), bothTeam: m.laneOf(both, "team"),
    };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"queuedTeam":"in-queue","queuedMine":"awaiting-review","runningTeam":"in-progress","runningMine":"in-review","oldTeam":"open","bothTeam":"in-progress"}' ]
}
