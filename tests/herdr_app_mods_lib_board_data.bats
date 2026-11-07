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
      calls.push({ cmd, args: [...args], cwd: opts && opts.cwd, on: opts && opts.on });
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

_rest() {
  printf '%s' '{"number":3,"title":"t","user":{"login":"ada"},"head":{"ref":"feat","sha":"abc"},"base":{"ref":"main"},"draft":false,"html_url":"https://ex/3","labels":[{"name":"bug"}],"created_at":"c","updated_at":"u"}'
}

_gql() {
  printf '%s' '{"data":{"repository":{"pr0":{"isDraft":false,"reviewDecision":"APPROVED","mergeable":"MERGEABLE","additions":1,"deletions":2,"changedFiles":3,"mergeQueueEntry":null,"commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":"SUCCESS"}}}]},"latestReviews":{"nodes":[{"author":{"login":"bob"},"state":"APPROVED"}]},"reviewRequests":{"nodes":[]},"state":"OPEN","mergedAt":null}}}}'
}

@test "loadRepo ssh and https remotes" {
  _bd '
    let table = [{ prefix: "git remote get-url origin", stdout: "git@github.com:acme/box.git\n" }];
    const ssh = pack(await m.loadRepo(exec, ctx));
    const sshEvery = calls.every(everyOn);
    calls.length = 0;
    table = [{ prefix: "git remote get-url origin", stdout: "https://github.com/acme/box.git\n" }];
    const https = pack(await m.loadRepo(exec, ctx));
    process.stdout.write(JSON.stringify({
      ssh, https, sshEvery, httpsEvery: calls.every(everyOn),
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"slug":"acme/box"'* ]]
  [[ "$output" == *'"host":"github.com"'* ]]
  [[ "$output" == *'"sshEvery":true'* ]]
  [[ "$output" == *'"httpsEvery":true'* ]]
}

@test "loadRepo non-GitHub remote and null cwd makes no exec call" {
  _bd '
    let table = [{ prefix: "git remote get-url origin", stdout: "https://example.com/foo.git\n" }];
    const bad = pack(await m.loadRepo(exec, ctx));
    const nBad = calls.length;
    calls.length = 0;
    const none = pack(await m.loadRepo(exec, { cwd: null, on: "mac" }));
    process.stdout.write(JSON.stringify({ bad, nBad, none, nNone: calls.length }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"failed"'* ]]
  [[ "$output" == *'origin is not a GitHub remote'* ]]
  [[ "$output" == *'not in a repository'* ]]
  [[ "$output" == *'"nNone":0'* ]]
}

@test "loadUser trims login and passes cwd/on" {
  _bd '
    const table = [{ prefix: "gh api /user --jq .login", stdout: "  ada  \n" }];
    const r = pack(await m.loadUser(exec, ctx));
    process.stdout.write(JSON.stringify({ r, every: calls.every(everyOn), n: calls.length }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"login":"ada"'* ]]
  [[ "$output" == *'"every":true'* ]]
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
  [[ "$output" == *'"ci":"none"'* ]]
  [[ "$output" == *'"inMq":true'* ]]
}

@test "loadOpenPrs 25 PRs make exactly 2 graphql calls" {
  _bd '
    const rest = [];
    for (let i = 1; i <= 25; i++) {
      rest.push({number:i,title:"t",user:{login:"ada"},head:{ref:"b",sha:"s"},base:{ref:"main"},draft:false,html_url:"u",labels:[],created_at:null,updated_at:null});
    }
    const table = [
      { prefix: "gh api /repos/o/n/pulls", stdout: JSON.stringify(rest) },
      { prefix: "gh api graphql", stdout: JSON.stringify({ data: { repository: {} } }) },
    ];
    const r = pack(await m.loadOpenPrs(exec, ctx, "o/n", "ada"));
    const gql = calls.filter(c => c.cmd === "gh" && c.args[1] === "graphql");
    process.stdout.write(JSON.stringify({
      ok: r.ok, n: r.prs.length, gql: gql.length, every: calls.every(everyOn),
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"n":25'* ]]
  [[ "$output" == *'"gql":2'* ]]
  [[ "$output" == *'"every":true'* ]]
}

@test "loadOpenPrs one failing graphql batch is partial with defaults" {
  _bd '
    const rest = [];
    for (let i = 1; i <= 25; i++) {
      rest.push({number:i,title:"t",user:{login:"ada"},head:{ref:"b",sha:"s"},base:{ref:"main"},draft:false,html_url:"u",labels:[],created_at:null,updated_at:null});
    }
    const table = [
      { prefix: "gh api /repos/o/n/pulls", stdout: JSON.stringify(rest) },
      { prefix: "gh api graphql", stdout: JSON.stringify({ data: { repository: {} } }) },
    ];
    const inner = exec;
    let nGql = 0;
    exec = async (cmd, args, opts) => {
      const got = await inner(cmd, args, opts);
      if (args[1] === "graphql") {
        nGql += 1;
        if (nGql === 2) return { code: 1, stdout: "", stderr: "boom" };
      }
      return got;
    };
    const r = pack(await m.loadOpenPrs(exec, ctx, "o/n", "ada"));
    const late = r.prs[20];
    process.stdout.write(JSON.stringify({
      ok: r.ok, partial: r.partial, every: calls.every(everyOn),
      ci: late.ciStatus, mergeable: late.mergeable, reviews: late.latestReviews,
      req: late.requestedReviewers, n: r.prs.length,
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"partial":true'* ]]
  [[ "$output" == *'"ci":"none"'* ]]
  [[ "$output" == *'"mergeable":"UNKNOWN"'* ]]
  [[ "$output" == *'"reviews":[]'* ]]
}

@test "gh auth login stderr is kind auth" {
  _bd '
    const table = [{ prefix: "gh api /user", code: 1, stderr: "gh auth login required" }];
    const r = pack(await m.loadUser(exec, ctx));
    process.stdout.write(JSON.stringify({ r, every: calls.every(everyOn) }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"auth"'* ]]
  [[ "$output" == *'"every":true'* ]]
}

@test "exec reject with on set is unreachable; on null ENOENT is missing-tool" {
  _bd '
    let table = [{ prefix: "gh api /user", reject: { message: "lost", code: "ENOENT" } }];
    const unreach = pack(await m.loadUser(exec, ctx));
    calls.length = 0;
    const miss = pack(await m.loadUser(exec, { cwd: "/repo", on: null }));
    process.stdout.write(JSON.stringify({
      unreach, miss, n: calls.length, onNull: calls[0] && calls[0].on === null,
      cwdOk: calls[0] && calls[0].cwd === "/repo",
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"unreachable"'* ]]
  [[ "$output" == *'"kind":"missing-tool"'* ]]
  [[ "$output" == *'"onNull":true'* ]]
}

@test "bad JSON is kind bad-json" {
  _bd '
    const table = [{ prefix: "pr review --list --schema-version 1", stdout: "not-json" }];
    const r = pack(await m.loadListing(exec, ctx));
    process.stdout.write(JSON.stringify({ r, every: calls.every(everyOn) }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"bad-json"'* ]]
  [[ "$output" == *'"every":true'* ]]
}

@test "loadListing ok and missing pr" {
  _bd '
    let table = [{ prefix: "pr review --list --schema-version 1", stdout: JSON.stringify({ schema_version: 1, reviews_root: "/reviews", reviews: [] }) }];
    const ok = pack(await m.loadListing(exec, ctx));
    const everyOk = calls.every(everyOn);
    calls.length = 0;
    table = [{ prefix: "pr review --list --schema-version 1", code: 127, stderr: "" }];
    const miss = pack(await m.loadListing(exec, ctx));
    process.stdout.write(JSON.stringify({
      ok, miss, everyOk, everyMiss: calls.every(everyOn), root: ok.reviewsRoot,
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"reviewsRoot":"/reviews"'* ]]
  [[ "$output" == *'"root":"/reviews"'* ]]
  [[ "$output" == *'"kind":"missing-tool"'* ]]
  [[ "$output" == *'"everyOk":true'* ]]
}

@test "loadBusy no-run text is empty set; real status yields busy numbers" {
  _bd '
    let table = [{ prefix: "pr batch status --full --offline", stdout: "pr batch: no batch runs yet" }];
    const empty = pack(await m.loadBusy(exec, ctx, "o/n"));
    const everyEmpty = calls.every(everyOn);
    calls.length = 0;
    table = [{ prefix: "pr batch status --full --offline", stdout: JSON.stringify({ run: { items: [{ key: "o/n#7", status: "running" }, { key: "o/n#8", status: "queued" }, { key: "p/n#9", status: "running" }] } }) }];
    const busy = pack(await m.loadBusy(exec, ctx, "o/n"));
    process.stdout.write(JSON.stringify({
      empty, busy, everyEmpty, everyBusy: calls.every(everyOn),
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"busy":[]'* ]]
  [[ "$output" == *'"busy":[7,8]'* ]]
  [[ "$output" == *'"everyEmpty":true'* ]]
  [[ "$output" == *'"everyBusy":true'* ]]
}

@test "loadBatchDoc: non-zero, bad JSON, and reject are ok with doc null" {
  _bd '
    let table = [{ prefix: "pr batch status --full --offline", code: 1, stdout: "" }];
    const nz = pack(await m.loadBatchDoc(exec, ctx));
    calls.length = 0;
    table = [{ prefix: "pr batch status --full --offline", stdout: "not-json" }];
    const bad = pack(await m.loadBatchDoc(exec, ctx));
    calls.length = 0;
    table = [{ prefix: "pr batch status --full --offline", reject: { message: "boom" } }];
    const rej = pack(await m.loadBatchDoc(exec, ctx));
    process.stdout.write(JSON.stringify({ nz, bad, rej }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"nz":{"ok":true,"doc":null}'* ]]
  [[ "$output" == *'"bad":{"ok":true,"doc":null}'* ]]
  [[ "$output" == *'"rej":{"ok":true,"doc":null}'* ]]
}

@test "loadLanded excludes open PRs, drops closed-unmerged and old merges, respects cap order" {
  _bd '
    const now = Date.parse("2026-06-15T00:00:00.000Z");
    const day = 86400000;
    const iso = (ms) => new Date(ms).toISOString();
    const row = (n, at, ref) => ({
      repo: "o/n", mode: "pr", pr_number: n, host: "github.com",
      head_ref: ref, head_sha: "s", reviewed_at: at, status: "completed",
      post: null, history: [],
    });
    const listing = { reviews: [
      row(7, "2026-06-12", "open"),
      row(11, "2026-06-11", "new"),
      row(8, "2026-06-10", "landed"),
      row(9, "2026-06-09", "closed"),
      row(10, "2026-06-08", "old"),
    ] };
    const node = (over) => Object.assign({
      isDraft: false, reviewDecision: null, mergeable: "MERGEABLE",
      additions: 0, deletions: 0, changedFiles: 0, mergeQueueEntry: null,
      commits: { nodes: [] }, latestReviews: { nodes: [] }, reviewRequests: { nodes: [] },
      state: "MERGED", mergedAt: iso(now - 2 * day),
    }, over);
    const table = [{ prefix: "gh api graphql", stdout: JSON.stringify({ data: { repository: {
      pr0: node({ mergedAt: iso(now - 1 * day) }),
      pr1: node({}),
      pr2: node({ state: "CLOSED", mergedAt: null }),
      pr3: node({ mergedAt: iso(now - 40 * day) }),
    } } }) }];
    const r = pack(await m.loadLanded(exec, ctx, "o/n", listing, "github.com", [{ number: 7 }], now));
    const nums = r.prs.map((p) => p.number);
    const gql = calls.filter(c => c.args[1] === "graphql");
    const q = gql[0] ? gql[0].args.join(" ") : "";
    process.stdout.write(JSON.stringify({
      ok: r.ok, nums, every: calls.every(everyOn), gql: gql.length,
      order11then8: q.indexOf("number: 11") < q.indexOf("number: 8"),
      title: r.prs[0] && r.prs[0].title, branch: r.prs[0] && r.prs[0].branch,
      state: r.prs[0] && r.prs[0].state,
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"nums":[11,8]'* ]]
  [[ "$output" == *'"every":true'* ]]
  [[ "$output" == *'"order11then8":true'* ]]
  [[ "$output" == *'"title":"#11"'* ]]
  [[ "$output" == *'"branch":"new"'* ]]
  [[ "$output" == *'"state":"MERGED"'* ]]
}

@test "assemble sets busy and reviewRuns" {
  _bd '
    const table = [];
    const listing = [{
      repo: "o/n", mode: "pr", pr_number: 7, host: "github.com",
      head_sha: "a", post: null, reviewed_at: "2026-01-02", status: "completed",
      history: [],
    }];
    const out = m.assemble({
      openPrs: [{ number: 7, headSha: "a", requestedReviewers: [], latestReviews: [] }],
      landedPrs: [{ number: 8, headSha: "b", requestedReviewers: [], latestReviews: [] }],
      listing, slug: "o/n", host: "github.com", login: "me", busy: new Set([7]),
    });
    process.stdout.write(JSON.stringify({
      n: out.length, b7: out[0].busy, b8: out[1].busy,
      runs7: out[0].reviewRuns.length, runs8: out[1].reviewRuns.length,
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"n":2'* ]]
  [[ "$output" == *'"b7":true'* ]]
  [[ "$output" == *'"b8":false'* ]]
  [[ "$output" == *'"runs7":1'* ]]
  [[ "$output" == *'"runs8":0'* ]]
}

@test "checkTools parses present and missing by basename" {
  _bd '
    let table = [{ prefix: "sh -c ", stdout: "/usr/bin/pr\n/opt/homebrew/bin/gh\n" }];
    const some = pack(await m.checkTools(exec, ctx));
    const everySome = calls.every(everyOn);
    calls.length = 0;
    table = [{ prefix: "sh -c ", stdout: "" }];
    const none = pack(await m.checkTools(exec, ctx));
    process.stdout.write(JSON.stringify({ some, none, everySome, everyNone: calls.every(everyOn) }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"pr":true'* ]]
  [[ "$output" == *'"gh":true'* ]]
  [[ "$output" == *'"wt":false'* ]]
  [[ "$output" == *'"everySome":true'* ]]
  [[ "$output" == *'"everyNone":true'* ]]
}
