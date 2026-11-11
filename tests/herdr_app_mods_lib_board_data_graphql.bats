#!/usr/bin/env bats
# Tests for herdr-app board_data loaders (exec + ctx, never throw).
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

# $1 = JS statements; table, exec, ctx, calls, pack, m are in scope.
_bd() {
  run node --input-type=module -e "
    import * as m from '$REPO_ROOT/ai/herdr/app/mods/_lib/board_data.js';
    const calls = [];
    let exec = async (cmd, args, opts) => {
      // Remote calls are wrapped to put ~/.local/bin on PATH; the table speaks of the inner command.
      if (cmd === 'sh' && args[0] === '-c' && args[1].includes('exec \"\$@\"') && args[2] === 'sh') {
        [cmd, args] = [args[3], args.slice(4)];
      }
      calls.push({ cmd, args: [...args], cwd: opts && opts.cwd, on: opts && opts.on, timeout: opts && opts.timeout });
      const key = cmd + ' ' + args.join(' ');
      for (const row of table) {
        if (!key.startsWith(row.prefix)) continue;
        if (row.reject) {
          const err = new Error(row.reject.message || 'rejected');
          if (row.reject.code) err.code = row.reject.code;
          throw err;
        }
        return { code: row.code ?? 0, stdout: row.stdout ?? '', stderr: row.stderr ?? '' };
      }
      return { code: 1, stdout: '', stderr: 'unexpected ' + key };
    };
    function pack(r) {
      if (r && r.busy instanceof Set) return Object.assign({}, r, { busy: [...r.busy] });
      return r;
    }
    const ctx = { cwd: '/repo', on: 'mac' };
    function everyOn(c) {
      return c.cwd === '/repo' && c.on === 'mac';
    }
    $1
  "
}

@test "loadOpenPrs maps and merges one batch and passes cwd/on" {
  _bd '
    const rest = {number:3,title:"t",user:{login:"ada"},head:{ref:"feat",sha:"abc"},base:{ref:"main"},draft:false,html_url:"https://ex/3",labels:[{name:"bug"}],created_at:"c",updated_at:"u"};
    const pr0 = {isDraft:true,reviewDecision:"APPROVED",mergeable:"MERGEABLE",additions:1,deletions:2,changedFiles:3,mergeQueueEntry:{id:"q"},commits:{nodes:[{commit:{statusCheckRollup:{state:"SUCCESS"}}}]},latestReviews:{nodes:[{author:{login:"bob"},state:"APPROVED"}]},reviewRequests:{nodes:[]},state:"OPEN",mergedAt:null};
    const table = [
      { prefix: "gh api /repos/acme/box/pulls", stdout: JSON.stringify([rest]) },
      { prefix: "gh api graphql", stdout: JSON.stringify({ data: { repository: { pr0 } } }) },
    ];
    const r = pack(await m.loadOpenPrs(exec, ctx, "acme/box", "ada"));
    const pr = r.prs[0];
    process.stdout.write(JSON.stringify({
      ok: r.ok, partial: r.partial, every: calls.every(everyOn),
      n: calls.length, gql: calls.filter(c => c.args[1] === "graphql").length,
      number: pr.number, state: pr.state, isDraft: pr.isDraft, ci: pr.ciStatus,
      mergeable: pr.mergeable, inMq: pr.inMergeQueue, title: pr.title, author: pr.author,
      reviews: pr.latestReviews, req: pr.requestedReviewers, add: pr.additions,
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ok":true'* ]]
  [[ "$output" == *'"partial":false'* ]]
  [[ "$output" == *'"every":true'* ]]
  [[ "$output" == *'"gql":1'* ]]
  [[ "$output" == *'"isDraft":true'* ]]
  [[ "$output" == *'"state":"OPEN"'* ]]
  [[ "$output" == *'"ci":"passing"'* ]]
  [[ "$output" == *'"inMq":true'* ]]
}

@test "loadOpenPrs 260 PRs make 6 graphql calls of at most 50, at most 4 at once" {
  _bd '
    const rest = [];
    for (let i = 1; i <= 260; i++) {
      rest.push({number:i,title:"t",user:{login:"ada"},head:{ref:"b",sha:"s"},base:{ref:"main"},draft:false,html_url:"u",labels:[],created_at:null,updated_at:null});
    }
    const table = [
      { prefix: "gh api /repos/o/n/pulls", stdout: JSON.stringify(rest) },
      { prefix: "gh api graphql", stdout: JSON.stringify({ data: { repository: {} } }) },
    ];
    const inner = exec;
    let inflight = 0;
    let max = 0;
    exec = async (cmd, args, opts) => {
      const innerArgs = cmd === "sh" ? args.slice(4) : args;
      const gqlCall = innerArgs[1] === "graphql";
      if (gqlCall) {
        inflight += 1;
        if (inflight > max) max = inflight;
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
      try {
        return await inner(cmd, args, opts);
      } finally {
        if (gqlCall) inflight -= 1;
      }
    };
    const r = pack(await m.loadOpenPrs(exec, ctx, "o/n", "ada"));
    const gql = calls.filter(c => c.cmd === "gh" && c.args[1] === "graphql");
    const sizes = gql.map(c => (c.args.join(" ").match(/pullRequest\(/g) || []).length);
    process.stdout.write(JSON.stringify({
      ok: r.ok, n: r.prs.length, gql: gql.length, every: calls.every(everyOn),
      atMost50: sizes.every(s => s <= 50), bounded: max <= 4 && max > 1, max,
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"n":260'* ]]
  [[ "$output" == *'"gql":6'* ]]
  [[ "$output" == *'"every":true'* ]]
  [[ "$output" == *'"atMost50":true'* ]]
  [[ "$output" == *'"bounded":true'* ]]
}

@test "loadOpenPrs one failing graphql batch is partial with defaults" {
  _bd '
    const rest = [];
    for (let i = 1; i <= 75; i++) {
      rest.push({number:i,title:"t",user:{login:"ada"},head:{ref:"b",sha:"s"},base:{ref:"main"},draft:false,html_url:"u",labels:[],created_at:null,updated_at:null});
    }
    const repo = {};
    for (let i = 0; i < 50; i++) repo["pr" + i] = {};
    const table = [
      { prefix: "gh api /repos/o/n/pulls", stdout: JSON.stringify(rest) },
      { prefix: "gh api graphql", stdout: JSON.stringify({ data: { repository: repo } }) },
    ];
    const inner = exec;
    exec = async (cmd, args, opts) => {
      const innerArgs = cmd === "sh" ? args.slice(4) : args;
      if (innerArgs[1] === "graphql" && innerArgs.join(" ").includes("number: 51)")) {
        return { code: 1, stdout: "", stderr: "boom" };
      }
      return inner(cmd, args, opts);
    };
    const r = pack(await m.loadOpenPrs(exec, ctx, "o/n", "ada"));
    const late = r.prs[60];
    process.stdout.write(JSON.stringify({
      ok: r.ok, partial: r.partial, every: calls.every(everyOn),
      ci: late.ciStatus, mergeable: late.mergeable, reviews: late.latestReviews,
      req: late.requestedReviewers, n: r.prs.length,
      unknownLate: late.ciUnknown, unknownFirst: r.prs[0].ciUnknown,
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"partial":true'* ]]
  [[ "$output" == *'"ci":"none"'* ]]
  [[ "$output" == *'"mergeable":"UNKNOWN"'* ]]
  [[ "$output" == *'"reviews":[]'* ]]
  [[ "$output" == *'"unknownLate":true'* ]]
  [[ "$output" == *'"unknownFirst":false'* ]]
}

@test "loadOpenPrs GraphQL success with a missing alias marks that PR ciUnknown" {
  _bd '
    const rest = [
      {number:3,title:"t",user:{login:"ada"},head:{ref:"feat",sha:"abc"},base:{ref:"main"},draft:false,html_url:"https://ex/3",labels:[],created_at:"c",updated_at:"u"},
      {number:4,title:"t",user:{login:"ada"},head:{ref:"feat",sha:"abc"},base:{ref:"main"},draft:false,html_url:"https://ex/4",labels:[],created_at:"c",updated_at:"u"},
    ];
    const pr0 = {isDraft:false,reviewDecision:null,mergeable:"MERGEABLE",additions:1,deletions:2,changedFiles:3,mergeQueueEntry:null,commits:{nodes:[{commit:{statusCheckRollup:{state:"SUCCESS"}}}]},latestReviews:{nodes:[]},reviewRequests:{nodes:[]},state:"OPEN",mergedAt:null};
    const table = [
      { prefix: "gh api /repos/acme/box/pulls", stdout: JSON.stringify(rest) },
      { prefix: "gh api graphql", stdout: JSON.stringify({ data: { repository: { pr0 } } }) },
    ];
    const r = pack(await m.loadOpenPrs(exec, ctx, "acme/box", "ada"));
    process.stdout.write(JSON.stringify({
      ok: r.ok, partial: r.partial, every: calls.every(everyOn),
      a: { n: r.prs[0].number, unknown: r.prs[0].ciUnknown },
      b: { n: r.prs[1].number, unknown: r.prs[1].ciUnknown },
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ok":true'* ]]
  [[ "$output" == *'"partial":false'* ]]
  [[ "$output" == *'"every":true'* ]]
  [[ "$output" == *'"a":{"n":3,"unknown":false}'* ]]
  [[ "$output" == *'"b":{"n":4,"unknown":true}'* ]]
}

@test "loadLanded with 100 candidates makes 2 graphql calls" {
  _bd '
    const now = Date.parse("2026-06-15T00:00:00.000Z");
    const reviews = [];
    for (let i = 1; i <= 100; i++) {
      reviews.push({
        repo: "o/n", mode: "pr", pr_number: i, host: "github.com",
        head_ref: "b", head_sha: "s", reviewed_at: "2026-06-12", status: "completed",
        post: null, history: [],
      });
    }
    const table = [{ prefix: "gh api graphql", stdout: JSON.stringify({ data: { repository: {} } }) }];
    const r = pack(await m.loadLanded(exec, ctx, "o/n", { reviews }, "github.com", [], now));
    const gql = calls.filter(c => c.cmd === "gh" && c.args[1] === "graphql");
    process.stdout.write(JSON.stringify({
      ok: r.ok, partial: r.partial, gql: gql.length,
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"gql":2'* ]]
  [[ "$output" == *'"ok":true'* ]]
  [[ "$output" == *'"partial":false'* ]]
}
