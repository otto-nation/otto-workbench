#!/usr/bin/env bats
# Tests for herdr-app team-prs action table.
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

# Evaluate EXPR against actions.js as m; print JSON.stringify(EXPR).
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/actions.js');
    const ctx = (over) => Object.assign({
      repo: 'o/n', number: 7, repoDir: '/w', run: null,
      headMoved: false, batchBusy: false, live: null, finished: null,
    }, over);
    const done = (over) => Object.assign({
      status: 'completed', head_sha: 'abc1234deadbeef', post: null,
    }, over);
    const ids = (over) => m.actionsFor(ctx(over)).filter(a => a.enabled).map(a => a.id);
    const posted = (event) => ids({run: done({post: {event}})});
    process.stdout.write(JSON.stringify($1));
  "
}

@test "exact argv per id for N=7 D=/w" {
  _js 'm.ACTION_IDS.map(id => m.prArgv(id, 7, "/w"))'
  [ "$status" -eq 0 ]
  [ "$output" = '[["pr","review","--no-post","--pr","7","--repo-dir","/w"],["pr","review","--no-post","--force","--pr","7","--repo-dir","/w"],["pr","review","--recover","--pr","7","--repo-dir","/w"],["pr","review","--post","--event","comment","--pr","7","--repo-dir","/w"],["pr","review","--post","--event","approve","--pr","7","--repo-dir","/w"],["pr","review","--post","--event","request_changes","--pr","7","--repo-dir","/w"],["pr","review","--dismiss","--pr","7","--repo-dir","/w"],null,null]' ]
}

@test "no bare 7 unless preceded by --pr" {
  _js 'm.ACTION_IDS.map(id => m.prArgv(id, 7, "/w")).filter(Boolean).every(a => a.every((x, i) => x !== "7" || a[i - 1] === "--pr"))'
  [ "$status" -eq 0 ]
  [ "$output" = 'true' ]
}

@test "run undefined disables all pr actions with Review state unknown" {
  _js 'm.actionsFor(ctx({run:undefined})).filter(a => a.id !== "cancel" && a.id !== "watch").map(a => [a.enabled, a.reason])'
  [ "$status" -eq 0 ]
  [ "$output" = '[[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"]]' ]
}

@test "missing repoDir disables all pr actions with No checkout for this repo" {
  _js 'm.actionsFor(ctx({repoDir:null,run:done()})).filter(a => a.id !== "cancel" && a.id !== "watch").map(a => [a.enabled, a.reason])'
  [ "$status" -eq 0 ]
  [ "$output" = '[[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"]]' ]
}

@test "no run enables only review" {
  _js 'ids({})'
  [ "$status" -eq 0 ]
  [ "$output" = '["review"]' ]
}

@test "unposted completed unmoved enables rerun, three posts, dismiss" {
  _js 'ids({run:done()})'
  [ "$status" -eq 0 ]
  [ "$output" = '["rerun","post-comment","post-approve","post-request-changes","dismiss"]' ]
}

@test "headMoved turns posts off and leaves rerun on" {
  _js 'ids({run:done(),headMoved:true})'
  [ "$status" -eq 0 ]
  [ "$output" = '["rerun","dismiss"]' ]
}

@test "posted and dismissed turn posts and dismiss off" {
  _js '[posted("comment"),posted("dismissed")]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["rerun"],["rerun"]]' ]
}

@test "partial and error enable recover and turn posts off" {
  _js '[ids({run:done({status:"partial"})}),ids({run:done({status:"error"})})]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["rerun","recover","dismiss"],["rerun","recover","dismiss"]]' ]
}

@test "batchBusy disables all pr actions and cancel with Started by pr batch" {
  _js '[ids({batchBusy:true,run:done()}),m.actionsFor(ctx({batchBusy:true})).find(a => a.id === "cancel").reason]'
  [ "$status" -eq 0 ]
  [ "$output" = '[[],"Started by pr batch"]' ]
}

@test "live homelab/t7 disables pr actions; cancel and watch target it" {
  _js '(()=>{const a=m.actionsFor(ctx({live:"homelab/t7",run:done()}));return [a.filter(x=>x.enabled).map(x=>x.id),a.find(x=>x.id==="cancel").command,a.find(x=>x.id==="watch").command];})()'
  [ "$status" -eq 0 ]
  [ "$output" = '[["cancel","watch"],{"name":"terminal.kill","args":{"terminal":"homelab/t7"}},{"name":"terminal.keep","args":{"terminal":"homelab/t7"}}]' ]
}

@test "finished only enables watch; cancel stays off" {
  _js '[ids({finished:"t9"}),m.actionsFor(ctx({finished:"t9"})).find(a => a.id === "cancel").reason]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["review","watch"],"Nothing running"]' ]
}

@test "confirm sha is 7 chars and event matches" {
  _js 'm.actionsFor(ctx({run:done()})).filter(a => a.confirm).map(a => ({id:a.id,pr:a.confirm.pr,event:a.confirm.event,sha:a.confirm.sha}))'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"id":"post-comment","pr":"o/n#7","event":"comment","sha":"abc1234"},{"id":"post-approve","pr":"o/n#7","event":"approve","sha":"abc1234"},{"id":"post-request-changes","pr":"o/n#7","event":"request_changes","sha":"abc1234"}]' ]
}

@test "actionsFor follows ACTION_IDS order" {
  _js '[m.ACTION_IDS,m.actionsFor(ctx()).map(a => a.id)]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["review","rerun","recover","post-comment","post-approve","post-request-changes","dismiss","cancel","watch"],["review","rerun","recover","post-comment","post-approve","post-request-changes","dismiss","cancel","watch"]]' ]
}
