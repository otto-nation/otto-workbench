#!/usr/bin/env bats
# Tests for herdr-app Has Comments data: pr's comments-only plan read into the board.
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
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/board_data.js');
    $1
  "
}

@test "owedFrom keeps only this repo's rows whose comments need is set" {
  _js '
    const doc = { rows: [
      { repo: "O/N", pr: 7, needs: { comments: { needed: true, reason: "2 unresolved threads" } } },
      { repo: "o/n", pr: 8, needs: { comments: { needed: false, reason: "no unresolved threads" } } },
      { repo: "o/other", pr: 9, needs: { comments: { needed: true, reason: "x" } } },
      { repo: "o/n", pr: "10", needs: { comments: { needed: true, reason: "x" } } },
      { repo: "o/n", pr: 11, needs: {} },
    ] };
    process.stdout.write(JSON.stringify([[...m.owedFrom(doc, "o/n")], [...m.owedFrom(null, "o/n")]]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[[[7,"2 unresolved threads"]],[]]' ]
}

@test "loadOwed runs the comments-only plan in the checkout and maps it" {
  _js '
    const seen = [];
    const exec = async (cmd, args, opts) => {
      seen.push([cmd, ...args, opts.cwd]);
      return { code: 0, stdout: JSON.stringify({ rows: [
        { repo: "o/n", pr: 3, needs: { comments: { needed: true, reason: "1 PR comment" } } }] }), stderr: "" };
    };
    const r = await m.loadOwed(exec, { cwd: "/co", on: null }, "o/n");
    process.stdout.write(JSON.stringify({ seen, ok: r.ok, owed: [...r.owed] }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"seen":[["pr","batch","plan","--checkout","/co","--needs","comments","/co"]],"ok":true,"owed":[[3,"1 PR comment"]]}' ]
}

@test "assemble puts each PR's owed feedback on it, null when none is known" {
  _js '
    const out = m.assemble({
      openPrs: [{ number: 7, headSha: "a", requestedReviewers: [], latestReviews: [] },
                { number: 8, headSha: "b", requestedReviewers: [], latestReviews: [] }],
      landedPrs: [], listing: [], slug: "o/n", host: "github.com", login: "me",
      busy: new Set(), owed: new Map([[7, "2 unresolved threads"]]),
    });
    const bare = m.assemble({ openPrs: [{ number: 7, headSha: "a", requestedReviewers: [], latestReviews: [] }],
      landedPrs: [], listing: [], slug: "o/n", host: "github.com", login: "me", busy: new Set() });
    process.stdout.write(JSON.stringify([out.map((p) => p.owedFeedback), bare[0].owedFeedback]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["2 unresolved threads",null],null]' ]
}

@test "owedDue is true when owedAt is missing or the ttl has elapsed" {
  _js '
    const t = 1e12;
    const ttl = m.OWED_TTL_MS;
    process.stdout.write(JSON.stringify([
      m.owedDue(undefined, t),
      m.owedDue(null, t),
      m.owedDue(NaN, t),
      m.owedDue(t - ttl, t),
      m.owedDue(t - ttl - 1, t),
      m.owedDue(t - ttl + 1, t),
      m.owedDue(t - 10, t, 10),
      m.owedDue(t - 9, t, 10),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,true,true,true,true,false,true,false]' ]
}
