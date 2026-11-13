#!/usr/bin/env bats
# Tests for herdr-app PRs board action table.
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
      features: ['review-publish'],
    }, over);
    const done = (over) => Object.assign({
      status: 'completed', head_sha: 'abc1234deadbeef', post: null,
    }, over);
    const ids = (over) => m.actionsFor(ctx(over)).filter(a => a.enabled).map(a => a.id);
    const posted = (event) => ids({run: done({post: {event}})});
    const prs = (over) => m.actionsFor(ctx(over)).filter(a => a.id !== 'cancel' && a.id !== 'watch');
    process.stdout.write(JSON.stringify($1));
  "
}

@test "exact argv per id for N=7 D=/w" {
  _js 'm.ACTION_IDS.map(id => m.prArgv(id, 7, "/w"))'
  [ "$status" -eq 0 ]
  [ "$output" = '[["pr","review","--no-post","--pr","7","--repo-dir","/w"],["pr","review","--no-post","--force","--pr","7","--repo-dir","/w"],["pr","review","--recover","--pr","7","--repo-dir","/w"],["pr","review","--post","--pr","7","--repo-dir","/w"],["pr","review","--post","--publish","comment","--pr","7","--repo-dir","/w"],["pr","review","--post","--publish","approve","--pr","7","--repo-dir","/w"],["pr","review","--post","--publish","request_changes","--pr","7","--repo-dir","/w"],["pr","review","--publish","comment","--pr","7","--repo-dir","/w"],["pr","review","--publish","approve","--pr","7","--repo-dir","/w"],["pr","review","--publish","request_changes","--pr","7","--repo-dir","/w"],["pr","review","--dismiss","--pr","7","--repo-dir","/w"],null,null]' ]
}

@test "no bare 7 unless preceded by --pr" {
  _js 'm.ACTION_IDS.map(id => m.prArgv(id, 7, "/w")).filter(Boolean).every(a => a.every((x, i) => x !== "7" || a[i - 1] === "--pr"))'
  [ "$status" -eq 0 ]
  [ "$output" = 'true' ]
}

@test "run undefined disables all pr actions with Review state unknown" {
  _js 'prs({run:undefined}).map(a => [a.enabled, a.reason])'
  [ "$status" -eq 0 ]
  [ "$output" = '[[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"],[false,"Review state unknown"]]' ]
}

@test "missing repoDir disables all pr actions with No checkout for this repo" {
  _js 'prs({repoDir:null,run:done()}).map(a => [a.enabled, a.reason])'
  [ "$status" -eq 0 ]
  [ "$output" = '[[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"],[false,"No checkout for this repo"]]' ]
}

@test "no run enables only review" {
  _js 'ids({})'
  [ "$status" -eq 0 ]
  [ "$output" = '["review"]' ]
}

@test "unposted with features enables rerun, post-draft, three posts, dismiss" {
  _js 'ids({run:done()})'
  [ "$status" -eq 0 ]
  [ "$output" = '["rerun","post-draft","post-comment","post-approve","post-request-changes","dismiss"]' ]
}

@test "pending enables rerun and the three publish actions" {
  _js 'ids({run:done({post:{event:"pending"}})})'
  [ "$status" -eq 0 ]
  [ "$output" = '["rerun","publish-comment","publish-approve","publish-request-changes"]' ]
}

@test "pending with headMoved enables only rerun" {
  _js 'ids({run:done({post:{event:"pending"}}),headMoved:true})'
  [ "$status" -eq 0 ]
  [ "$output" = '["rerun"]' ]
}

@test "headMoved turns posts off and leaves rerun on" {
  _js 'ids({run:done(),headMoved:true})'
  [ "$status" -eq 0 ]
  [ "$output" = '["rerun","dismiss"]' ]
}

@test "published comment/approve and dismissed enable only rerun" {
  _js '[posted("comment"),posted("approve"),posted("dismissed")]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["rerun"],["rerun"],["rerun"]]' ]
}

@test "no review-publish feature disables post and publish with the update reason" {
  _js '(()=>{const a=prs({run:done(),features:[]});const posts=a.filter(x=>x.id.startsWith("post-")||x.id.startsWith("publish-"));return [ids({run:done(),features:[]}),posts.every(x=>!x.enabled),posts[0].reason];})()'
  [ "$status" -eq 0 ]
  [ "$output" = '[["rerun","dismiss"],true,"Update pr on this machine to post (otto-workbench sync)"]' ]
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
  _js '(()=>{const a=m.actionsFor(ctx({live:"homelab/t7",run:done()}));const w=a.find(x=>x.id==="watch");return [a.filter(x=>x.enabled).map(x=>x.id),a.find(x=>x.id==="cancel").command,w.show,w.command];})()'
  [ "$status" -eq 0 ]
  [ "$output" = '[["cancel","watch"],{"name":"terminal.kill","args":{"terminal":"homelab/t7"}},"homelab/t7",null]' ]
}

@test "finished only enables watch; cancel stays off" {
  _js '[ids({finished:"t9"}),m.actionsFor(ctx({finished:"t9"})).find(a => a.id === "cancel").reason,m.actionsFor(ctx({finished:"t9"})).find(a => a.id === "watch").show]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["review","watch"],"Nothing running","t9"]' ]
}

@test "confirm messages and events per post id" {
  _js 'm.actionsFor(ctx({run:done()})).filter(a => a.confirm).map(a => ({id:a.id,pr:a.confirm.pr,event:a.confirm.event,sha:a.confirm.sha,message:a.confirm.message}))'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"id":"post-draft","pr":"o/n#7","event":null,"sha":"abc1234","message":"Post draft on o/n#7? Puts the review of abc1234 on GitHub as a draft only you can see."},{"id":"post-comment","pr":"o/n#7","event":"comment","sha":"abc1234","message":"Post comment on o/n#7? Publishes the review of abc1234 as a comment."},{"id":"post-approve","pr":"o/n#7","event":"approve","sha":"abc1234","message":"Approve o/n#7? Publishes the review of abc1234 as an approval."},{"id":"post-request-changes","pr":"o/n#7","event":"request_changes","sha":"abc1234","message":"Request changes on o/n#7? Publishes the review of abc1234 as a change request."},{"id":"publish-comment","pr":"o/n#7","event":"comment","sha":"abc1234","message":"Publish draft on o/n#7? Publishes the draft of abc1234 as a comment."},{"id":"publish-approve","pr":"o/n#7","event":"approve","sha":"abc1234","message":"Approve o/n#7? Publishes the draft of abc1234 as an approval."},{"id":"publish-request-changes","pr":"o/n#7","event":"request_changes","sha":"abc1234","message":"Request changes on o/n#7? Publishes the draft of abc1234 as a change request."}]' ]
}

@test "actionsFor follows ACTION_IDS order" {
  _js '[m.ACTION_IDS,m.actionsFor(ctx()).map(a => a.id)]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["review","rerun","recover","post-draft","post-comment","post-approve","post-request-changes","publish-comment","publish-approve","publish-request-changes","dismiss","cancel","watch"],["review","rerun","recover","post-draft","post-comment","post-approve","post-request-changes","publish-comment","publish-approve","publish-request-changes","dismiss","cancel","watch"]]' ]
}
