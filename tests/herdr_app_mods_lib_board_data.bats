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

_rr() {
  run node --input-type=module -e "
    import * as m from '$REPO_ROOT/ai/herdr/app/mods/_lib/board_data.js';
    $1
  "
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

@test "loadOpenPrs reads one page per line" {
  _bd '
    const restA = {number:3,title:"t",user:{login:"ada"},head:{ref:"feat",sha:"abc"},base:{ref:"main"},draft:false,html_url:"https://ex/3",labels:[{name:"bug"}],created_at:"c",updated_at:"u"};
    const restB = {number:4,title:"t",user:{login:"ada"},head:{ref:"feat",sha:"abc"},base:{ref:"main"},draft:false,html_url:"https://ex/4",labels:[{name:"bug"}],created_at:"c",updated_at:"u"};
    const table = [
      { prefix: "gh api /repos/acme/box/pulls", stdout: JSON.stringify([restA]) + "\n" + JSON.stringify([restB]) + "\n" },
      { prefix: "gh api graphql", stdout: JSON.stringify({ data: { repository: {} } }) },
    ];
    const r = await m.loadOpenPrs(exec, ctx, "acme/box", "ada");
    process.stdout.write(JSON.stringify({ ok: r.ok, nums: r.prs.map(p => p.number) }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ok":true'* ]]
  [[ "$output" == *'"nums":[3,4]'* ]]
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
      if ((cmd === "sh" ? args.slice(4) : args)[1] === "graphql") {
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

@test "exec reject with on set is exec with the command; on null ENOENT is missing-tool" {
  _bd '
    let table = [{ prefix: "gh api /user", reject: { message: "`herdr.exec`: hc exec: " } }];
    const remote = pack(await m.loadUser(exec, ctx));
    calls.length = 0;
    const miss = pack(await m.loadUser(exec, { cwd: "/repo", on: null }));
    process.stdout.write(JSON.stringify({
      remote, miss, n: calls.length, onNull: calls[0] && calls[0].on === null,
      cwdOk: calls[0] && calls[0].cwd === "/repo",
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"exec"'* ]]
  [[ "$output" == *'"command":"gh api /user"'* ]]
  [[ "$output" == *'"message":""'* ]]
  [[ "$output" == *'"kind":"missing-tool"'* ]]
  [[ "$output" == *'"onNull":true'* ]]
}

@test "a remote exec timeout is kind timeout" {
  _bd '
    const table = [{ prefix: "gh api /user", reject: { message: "`herdr.exec`: gh took longer than 30 s on mac and was stopped" } }];
    const r = await m.loadUser(exec, ctx);
    process.stdout.write(JSON.stringify(r));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"timeout"'* ]]
  [[ "$output" == *'"message":"gh took longer than 30 s on mac and was stopped"'* ]]
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

@test "loadListing asks for a 120 s timeout" {
  _bd '
    const table = [{ prefix: "pr review --list", stdout: JSON.stringify({ reviews_root: "/r" }) }];
    await m.loadListing(exec, ctx);
    process.stdout.write(JSON.stringify({ t: calls.find(c => c.cmd === "pr").timeout }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"t":120000'* ]]
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

@test "assemble running map marks the PR reviewing" {
  _bd '
    const table = [];
    const out = m.assemble({
      openPrs: [{ number: 7, headSha: "a", requestedReviewers: [], latestReviews: [] }],
      listing: { reviews: [] }, slug: "o/n", host: "github.com", login: "me",
      busy: new Set(), running: new Map([[7, { terminal: "t7" }]]),
    });
    process.stdout.write(JSON.stringify({ r: out[0].reviewing, t: out[0].reviewTerminal }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"r":true'* ]]
  [[ "$output" == *'"t":"t7"'* ]]
}

@test "assemble without running leaves PRs not reviewing" {
  _bd '
    const out = m.assemble({
      openPrs: [{ number: 7, headSha: "a", requestedReviewers: [], latestReviews: [] }],
      listing: { reviews: [] }, slug: "o/n", host: "github.com", login: "me", busy: new Set(),
    });
    process.stdout.write(JSON.stringify({ r: out[0].reviewing }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"r":false'* ]]
}

@test "loadTerminals lists by app label then reads each with hc get" {
  _rr '
    const ctx = { cwd: "/repo" };
    const calls = [];
    const fake = async (cmd, args) => {
      calls.push(args.join(" "));
      if (args[0] === "ls") return { code: 0, stdout: JSON.stringify({ terminals: [{ address: "t7" }, { address: "homelab/t8" }] }) };
      const id = args[1];
      if (id === "t7") return { code: 0, stdout: JSON.stringify({ address: id, state: "live", labels: { "otto.pr": "o/n#7" } }) };
      return { code: 1, stdout: "", stderr: "gone" };
    };
    const ok = await m.loadTerminals(fake, ctx);
    const fail = await m.loadTerminals(async () => ({ code: 1, stdout: "", stderr: "no" }), ctx);
    const threw = await m.loadTerminals(async () => { throw new Error("lost"); }, ctx);
    const bad = await m.loadTerminals(async () => ({ code: 0, stdout: "not json" }), ctx);
    process.stdout.write(JSON.stringify({
      ids: ok.entries.map((e) => e.id), pr: ok.entries[0].labels["otto.pr"], calls,
      fail, threw, badOk: bad.ok, badN: bad.entries.length,
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ids":["t7"]'* ]]
  [[ "$output" == *'"pr":"o/n#7"'* ]]
  [[ "$output" == *'"calls":["ls --json --label otto.app=pr","get t7 --json","get homelab/t8 --json"]'* ]]
  [[ "$output" == *'"fail":{"ok":false,"entries":[]}'* ]]
  [[ "$output" == *'"threw":{"ok":false,"entries":[]}'* ]]
  [[ "$output" == *'"badOk":true'* ]]
  [[ "$output" == *'"badN":0'* ]]
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

@test "readReview ok text and missing file is text null" {
  _rr '
    const calls = [];
    const readTextFile = async (path, opts) => {
      calls.push({ path, on: opts && opts.on });
      if (path === "/gone.md") return null;
      return "# review";
    };
    const ok = await m.readReview(readTextFile, "/r.md", "mac");
    const miss = await m.readReview(readTextFile, "/gone.md", "mac");
    process.stdout.write(JSON.stringify({ ok, miss, calls }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ok":true'* ]]
  [[ "$output" == *'"text":"# review"'* ]]
  [[ "$output" == *'"text":null'* ]]
  [[ "$output" == *'"path":"/r.md"'* ]]
  [[ "$output" == *'"on":"mac"'* ]]
}

@test "readReview reject with on is exec; without on is failed" {
  _rr '
    const readTextFile = async () => { throw new Error("lost"); };
    const withOn = await m.readReview(readTextFile, "/r.md", "mac");
    const noOn = await m.readReview(readTextFile, "/r.md", null);
    process.stdout.write(JSON.stringify({ withOn, noOn }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"exec"'* ]]
  [[ "$output" == *'"kind":"failed"'* ]]
}

@test "toolCall leaves local calls alone and wraps remote ones with the workbench bin on PATH" {
  _bd '
    process.stdout.write(JSON.stringify([
      m.toolCall({ on: null }, "pr", ["review", "--list"]),
      m.toolCall({ on: "" }, "gh", ["api"]),
      m.toolCall({ on: "homelab" }, "pr", ["review", "--list"]),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"cmd":"pr","args":["review","--list"]},{"cmd":"gh","args":["api"]},{"cmd":"sh","args":["-c","PATH=\"$HOME/.local/bin:$PATH\"; exec \"$@\"","sh","pr","review","--list"]}]' ]
}

@test "loadListing sends the prior stamp as --if-changed and keeps the prior when unchanged" {
  _bd '
    const full = { schema_version: 1, reviews_root: "/reviews", stamp: "s1", reviews: [{ repo: "o/n" }] };
    let table = [{ prefix: "pr review --list --schema-version 1", stdout: JSON.stringify(full) }];
    const first = await m.loadListing(exec, ctx);
    const firstArgs = calls.map((c) => c.args.join(" ")).join("|");
    calls.length = 0;
    table = [{ prefix: "pr review --list --schema-version 1 --if-changed s1", stdout: JSON.stringify({ schema_version: 1, stamp: "s1", unchanged: true }) }];
    const second = await m.loadListing(exec, ctx, first);
    const secondArgs = calls.map((c) => c.args.join(" ")).join("|");
    process.stdout.write(JSON.stringify({
      firstIf: firstArgs.includes("--if-changed"), secondIf: secondArgs.includes("--if-changed s1"),
      same: second === first,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"firstIf":false,"secondIf":true,"same":true}' ]
}

@test "a remote pr that is coreutils' pr is named as the wrong pr, not shown as raw usage" {
  _bd '
    const table = [{ prefix: "pr review --list", code: 1, stderr: "pr: unrecognized option \x27--list\x27\nTry \x27pr --help\x27 for more information." }];
    const r = await m.loadListing(exec, { cwd: "/w", on: "homelab" });
    process.stdout.write(JSON.stringify(r));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ok":false'* ]]
  [[ "$output" == *"a different \`pr\` is first on PATH"* ]]
  [[ "$output" != *"unrecognized option"* ]]
}

@test "loadFiles maps per-line pages and reports a gh failure" {
  _bd '
    const a = {filename:"a.js",status:"added",additions:1,deletions:0,patch:"@@ -0,0 +1 @@\n+x"};
    const b = {filename:"b.js",status:"modified",additions:1,deletions:0};
    const table = [{ prefix: "gh api /repos/o/n/pulls/7/files", stdout: JSON.stringify([a]) + "\n" + JSON.stringify([b]) + "\n" }];
    const ok = await m.loadFiles(exec, ctx, "o/n", 7);
    const bad = await m.loadFiles(async () => ({ code: 1, stdout: "", stderr: "boom" }), ctx, "o/n", 7);
    process.stdout.write(JSON.stringify([ok.files[0].path, ok.files[0].status, bad.ok, bad.error.message]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["a.js","A",false,"boom"]' ]
}

@test "loadFiles marks files .gitattributes calls generated, and skips that without a checkout" {
  _bd '
    const pages = JSON.stringify([{filename:"a.js",status:"modified",additions:1,deletions:0},{filename:"g.json",status:"modified",additions:9,deletions:9}]) + "\n";
    const table = [
      { prefix: "gh api /repos/o/n/pulls/7/files", stdout: pages },
      { prefix: "git check-attr -z linguist-generated -- a.js g.json", stdout: "a.js\0linguist-generated\0unspecified\0g.json\0linguist-generated\0set\0" },
    ];
    const withCwd = await m.loadFiles(exec, ctx, "o/n", 7);
    const none = await m.loadFiles(exec, { cwd: null, on: null }, "o/n", 7);
    process.stdout.write(JSON.stringify([withCwd.files.map(f=>f.generated), none.files.map(f=>f.generated)]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[[false,true],[false,false]]' ]
}

@test "loadPatch asks for the file's page and maps empty to null" {
  _bd '
    const table = [{ prefix: "gh api /repos/o/n/pulls/7/files?per_page=100&page=2", stdout: "@@ -1 +1 @@\n" }];
    const r1 = await m.loadPatch(exec, ctx, "o/n", 7, {path:"a.js", page:2});
    exec = async () => ({code:0, stdout:"", stderr:""});
    const r2 = await m.loadPatch(exec, ctx, "o/n", 7, {path:"a.js", page:2});
    process.stdout.write(JSON.stringify({a: r1.patch, b: r2.patch, ok: r1.ok && r2.ok}));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"a":"@@ -1 +1 @@"'* ]]
  [[ "$output" == *'"b":null'* ]]
  [[ "$output" == *'"ok":true'* ]]
}
