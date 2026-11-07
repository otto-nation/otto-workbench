#!/usr/bin/env bats
# Tests for herdr-app joining pr-review listing rows onto GitHub PR records.
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

# Evaluate EXPR against reviews.js as m; print JSON.stringify(EXPR).
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/reviews.js');
    const row = (over) => Object.assign({
      repo: 'o/n', mode: 'pr', pr_number: 7, host: 'github.com',
      head_sha: 'a', post: null, reviewed_at: '2026-01-02',
      status: 'completed', history: [],
    }, over);
    process.stdout.write(JSON.stringify($1));
  "
}

@test "reviewLabel format" {
  _js 'm.reviewLabel("o/n", 7)'
  [ "$status" -eq 0 ]
  [ "$output" = '"otto.pr=o/n#7"' ]
}

@test "joinReviews matches repo exactly" {
  _js 'm.joinReviews([{number:7,headSha:"a"}],[row({repo:"o/nn"}),row({repo:"p/n"}),row({repo:"o/n-x"})],"o/n","github.com","me")[0].reviewRuns'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "joinReviews host filter excludes other hosts and includes absent host" {
  _js '[m.joinReviews([{number:7,headSha:"a"}],[row({host:"ghe.example.com"})],"o/n","github.com","me")[0].reviewRuns.length,m.joinReviews([{number:7,headSha:"a"}],[(()=>{const r=row();delete r.host;return r})()],"o/n","github.com","me")[0].reviewRuns.length]'
  [ "$status" -eq 0 ]
  [ "$output" = '[0,1]' ]
}

@test "joinReviews ignores mode self rows" {
  _js 'm.joinReviews([{number:7,headSha:"a"}],[row({mode:"self"})],"o/n","github.com","me")[0].reviewRuns.length'
  [ "$status" -eq 0 ]
  [ "$output" = '0' ]
}

@test "history runs follow the row; sparse history entries pass through" {
  _js 'm.joinReviews([{number:7,headSha:"a"}],[row({head_sha:"c",history:[{head_sha:"b",post:{event:"comment"}}]})],"o/n","github.com","me")[0].reviewRuns.map(r=>({e:r.postedEvent,h:r.head_sha,s:r.status}))'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"e":null,"h":"c","s":"completed"},{"e":"comment","h":"b","s":null}]' ]
}

@test "postedEvent and posted() for null, dismissed, and empty event" {
  _js '[m.runsFor(row({post:null}))[0].postedEvent,m.runsFor(row({post:{event:"dismissed"}}))[0].postedEvent,m.runsFor(row({post:{event:""}}))[0].postedEvent,m.posted(row({post:null})),m.posted(row({post:{event:"dismissed"}})),m.posted(row({post:{event:""}}))]'
  [ "$status" -eq 0 ]
  [ "$output" = '[null,"dismissed","",false,true,true]' ]
}

@test "myReviewState: GitHub latestReviews wins over runs" {
  _js 'm.joinReviews([{number:7,headSha:"a",latestReviews:[{login:"me",state:"APPROVED"}]}],[row({post:{event:"comment"}})],"o/n","github.com","me")[0].myReviewState'
  [ "$status" -eq 0 ]
  [ "$output" = '"APPROVED"' ]
}

@test "myReviewState: DISMISSED and PENDING fall back to newest posted run" {
  _js 'm.joinReviews([{number:7,headSha:"a",latestReviews:[{login:"me",state:"DISMISSED"},{login:"me",state:"PENDING"}]}],[row({post:{event:"approve"}})],"o/n","github.com","me")[0].myReviewState'
  [ "$status" -eq 0 ]
  [ "$output" = '"APPROVED"' ]
}

@test "myReviewState maps each posted event" {
  _js '["approve","requested_changes","comment"].map(event=>m.joinReviews([{number:7,headSha:"a"}],[row({post:{event}})],"o/n","github.com","me")[0].myReviewState)'
  [ "$status" -eq 0 ]
  [ "$output" = '["APPROVED","CHANGES_REQUESTED","COMMENTED"]' ]
}

@test "myReviewState is null for a dismissed-only run" {
  _js 'm.joinReviews([{number:7,headSha:"a"}],[row({post:{event:"dismissed"}})],"o/n","github.com","me")[0].myReviewState'
  [ "$status" -eq 0 ]
  [ "$output" = 'null' ]
}

@test "myReviewState compares login case-insensitively" {
  _js 'm.joinReviews([{number:7,headSha:"a",latestReviews:[{login:"ME",state:"COMMENTED"}]}],[],"o/n","github.com","me")[0].myReviewState'
  [ "$status" -eq 0 ]
  [ "$output" = '"COMMENTED"' ]
}

@test "isRequestedReviewer true with no review and false once a review exists" {
  _js '[m.joinReviews([{number:7,headSha:"a",requestedReviewers:["ME"]}],[],"o/n","github.com","me")[0].isRequestedReviewer,m.joinReviews([{number:7,headSha:"a",requestedReviewers:["me"],latestReviews:[{login:"me",state:"APPROVED"}]}],[],"o/n","github.com","me")[0].isRequestedReviewer]'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,false]' ]
}

@test "hasNewChangesSinceReview true when head differs, false when same or no runs" {
  _js '[m.joinReviews([{number:7,headSha:"b"}],[row()],"o/n","github.com","me")[0].hasNewChangesSinceReview,m.joinReviews([{number:7,headSha:"a"}],[row()],"o/n","github.com","me")[0].hasNewChangesSinceReview,m.joinReviews([{number:7,headSha:"b"}],[],"o/n","github.com","me")[0].hasNewChangesSinceReview]'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,false,false]' ]
}

@test "reviewing is always false" {
  _js 'm.joinReviews([{number:7,headSha:"a"}],[row({post:{event:"approve"}})],"o/n","github.com","me")[0].reviewing'
  [ "$status" -eq 0 ]
  [ "$output" = 'false' ]
}

@test "health Partial, Error, and ok" {
  _js '[m.joinReviews([{number:7,headSha:"a"}],[row({status:"partial"})],"o/n","github.com","me")[0].health,m.joinReviews([{number:7,headSha:"a"}],[row({status:"error"})],"o/n","github.com","me")[0].health,m.joinReviews([{number:7,headSha:"a"}],[row()],"o/n","github.com","me")[0].health,m.joinReviews([{number:7,headSha:"a"}],[],"o/n","github.com","me")[0].health]'
  [ "$status" -eq 0 ]
  [ "$output" = '["Partial","Error","ok","ok"]' ]
}

@test "joinReviews does not mutate inputs" {
  _js '(()=>{const prs=[{number:7,headSha:"a",latestReviews:[{login:"me",state:"APPROVED"}]}];const listing=[row({post:{event:"comment"},history:[{head_sha:"z"}]})];const bp=JSON.stringify(prs),bl=JSON.stringify(listing);m.joinReviews(prs,listing,"o/n","github.com","me");return bp===JSON.stringify(prs)&&bl===JSON.stringify(listing)})()'
  [ "$status" -eq 0 ]
  [ "$output" = 'true' ]
}

@test "malformed listing yields empty reviewRuns for every PR" {
  _js '[null,{},"x"].map(listing=>m.joinReviews([{number:7,headSha:"a"}],listing,"o/n","github.com","me")[0].reviewRuns)'
  [ "$status" -eq 0 ]
  [ "$output" = '[[],[],[]]' ]
}

@test "landedCandidates excludes open PRs, newest first, unique, capped at 100" {
  _js '(()=>{const rows=[];for(let i=1;i<=120;i++)rows.push(row({pr_number:i,reviewed_at:String(i).padStart(3,"0")}));const capped=m.landedCandidates([],rows,"o/n","github.com");const excl=m.landedCandidates([{number:7}],[row({pr_number:7}),row({pr_number:8,reviewed_at:"2026-01-03"}),row({pr_number:8,reviewed_at:"2026-01-01"}),row({pr_number:9,reviewed_at:"2026-01-02"})],"o/n","github.com");return {len:capped.length,first:capped[0],last:capped[99],excl}})()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"len":100,"first":120,"last":21,"excl":[8,9]}' ]
}

@test "keepLanded keeps 29 days, drops 31 days, missing mergedAt, and closed-unmerged" {
  _js '(()=>{const now=Date.parse("2026-06-15T00:00:00.000Z"),day=86400000,iso=d=>new Date(d).toISOString();return m.keepLanded([{n:29,mergedAt:iso(now-29*day)},{n:31,mergedAt:iso(now-31*day)},{n:"missing"},{n:"unmerged",mergedAt:null},{n:"empty",mergedAt:""}],now).map(e=>e.n)})()'
  [ "$status" -eq 0 ]
  [ "$output" = '[29]' ]
}
