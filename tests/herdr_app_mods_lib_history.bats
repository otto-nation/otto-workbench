#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/history.js — review history view model.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() { load 'test_helper'; common_setup; }
teardown() { common_teardown; }

# $1 = JS expression over history.js exports. Prints JSON.
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/history.js');
    const { historyRows, relativeTime, formatTokens } = m;
    const now = Date.parse('2026-08-18T14:00:00Z');
    const iso = (ms) => new Date(ms).toISOString();
    const run = (over={}) => ({
      stamp: 's1', review_file: '/r.md', structured: true,
      head_sha: 'abcdef1234567890', reviewed_at: '2026-08-18T13:00:00Z',
      verdict: 'approve', findings: {must_fix:2,should_fix:1,nit:3,idiom:0,total:6},
      status: 'complete', cost_usd: 0.42, input_tokens: 20000, output_tokens: 11000,
      post: {event:'comment', posted_at:'', review_id:1, sha_drifted:false}, ...over,
    });
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}

@test "post labels: comment, approve, requested_changes, dismissed, unposted, unknown, empty" {
  _js 'historyRows([run({post:{event:"comment"}}),run({post:{event:"approve"}}),run({post:{event:"requested_changes"}}),run({post:{event:"dismissed"}}),run({post:null}),run({post:{event:"other"}}),run({post:{event:""}})], now).map(r=>r.post.label)'
  [ "$status" -eq 0 ]
  jq -e '.==["posted as comment","approved","changes requested","dismissed","unposted","posted","posted"]' <<<"$output"
}

@test "structured:false blanks verdict/sha/cost; findingsText is details not recorded" {
  _js 'historyRows([run({structured:false,verdict:"approve",head_sha:"abcdef1",cost_usd:1,findings:{must_fix:1}})], now)[0]'
  [ "$status" -eq 0 ]
  jq -e '.verdict=="" and .shortSha=="" and .costText=="" and .findingsText=="details not recorded" and .structured==false' <<<"$output"
}

@test "zero findings is no findings; counts join with middot; cost and sha" {
  _js '({a:historyRows([run({findings:{must_fix:0,should_fix:0,nit:0,idiom:0}})],now)[0].findingsText,b:historyRows([run()],now)[0]})'
  [ "$status" -eq 0 ]
  jq -e '.a=="no findings" and .b.findingsText=="2 must-fix · 1 should-fix · 3 nit" and .b.costText=="$0.42 · 31k tok" and .b.shortSha=="abcdef1" and .b.reviewFile=="/r.md"' <<<"$output"
}

@test "formatTokens: 0, 999, 1000, 31k, 1.2M" {
  _js '({a:formatTokens(0),b:formatTokens(999),c:formatTokens(1000),d:formatTokens(31000),e:formatTokens(1200000)})'
  [ "$status" -eq 0 ]
  jq -e '.a=="0" and .b=="999" and .c=="1k" and .d=="31k" and .e=="1.2M"' <<<"$output"
}

@test "relativeTime: 59s, 60s, 59m, 24h, 8d, future, garbage" {
  _js '({a:relativeTime(iso(now-59000),now),b:relativeTime(iso(now-60000),now),c:relativeTime(iso(now-59*60000),now),d:relativeTime(iso(now-24*3600000),now),e:relativeTime(iso(now-8*86400000),now),f:relativeTime(iso(now+60000),now),g:relativeTime("nope",now)})'
  [ "$status" -eq 0 ]
  jq -e '.a=="just now" and .b=="1m ago" and .c=="59m ago" and .d=="1d ago" and .e=="8d ago" and .f=="just now" and .g==""' <<<"$output"
}

@test "unparseable reviewed_at falls back to stamp; newest first; input not mutated" {
  _js '(() => { const r=[run({stamp:"old",reviewed_at:"2026-08-18T10:00:00Z"}),run({stamp:"new",reviewed_at:"2026-08-18T13:00:00Z"})]; const snap=JSON.stringify(r); const rows=historyRows(r,now); const garbage=historyRows([run({stamp:"STAMP",reviewed_at:"nope"})],now); return {order:rows.map(x=>x.stamp),when:garbage[0].when,kept:JSON.stringify(r)===snap}; })()'
  [ "$status" -eq 0 ]
  jq -e '.order==["new","old"] and .when=="STAMP" and .kept' <<<"$output"
}

@test "health: Partial, Error, ok; missing post is unposted" {
  _js '({a:historyRows([run({status:"partial"})],now)[0].health,b:historyRows([run({status:"error"})],now)[0].health,c:historyRows([run()],now)[0].health,d:historyRows([run({post:undefined})],now)[0].post.label,e:historyRows([run()],now)[0].when})'
  [ "$status" -eq 0 ]
  jq -e '.a=="Partial" and .b=="Error" and .c=="ok" and .d=="unposted" and .e=="1h ago"' <<<"$output"
}
