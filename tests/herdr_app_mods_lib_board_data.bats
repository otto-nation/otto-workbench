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
      if (cmd === 'sh' && args[0] === '-c' && args[1] === m.REMOTE_PREAMBLE && args[2] === 'sh') {
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

@test "loadListing uses the exec default timeout" {
  _bd '
    const table = [{ prefix: "pr review --list", stdout: JSON.stringify({ reviews_root: "/r" }) }];
    await m.loadListing(exec, ctx);
    process.stdout.write(JSON.stringify({ t: calls.find(c => c.cmd === "pr").timeout ?? "default" }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"t":"default"'* ]]
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
  [ "$output" = '[{"cmd":"pr","args":["review","--list"]},{"cmd":"gh","args":["api"]},{"cmd":"sh","args":["-c","PATH=\"$HOME/.local/bin:$PATH\"; if [ -r \"$HOME/.env.local\" ] && ( . \"$HOME/.env.local\" ) >/dev/null 2>&1; then set -a; . \"$HOME/.env.local\" >/dev/null 2>&1; set +a; fi; exec \"$@\"","sh","pr","review","--list"]}]' ]
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

@test "readReview on a remote answers from exec cat when readTextFile never settles" {
  _rr '
    const readTextFile = () => new Promise(() => {});
    const exec = async (cmd, args, opts) => ({ code: 0, stdout: "# from cat:" + cmd + args[0] + opts.on });
    const r = await m.readReview(readTextFile, "/r.md", "agent", exec);
    process.stdout.write(JSON.stringify(r));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"text":"# from cat:cat/r.mdagent"'* ]]
}

@test "remote loadBoard argv contains the isolation wrapper with the phase seconds and MemoryMax" {
  _rr '
    const calls = [];
    const exec = async (cmd, args, opts) => {
      calls.push({ cmd, args: [...args], timeout: opts.timeout, isolate: Object.hasOwn(opts, "isolate") });
      return { code: 1, stdout: "", stderr: "skip" };
    };
    for (const phase of ["listing", "status", "owed"]) {
      await m.loadBoard(exec, { cwd: "/w", on: "mac" }, phase);
    }
    process.stdout.write(JSON.stringify(calls.map((c) => ({
      cmd: c.cmd,
      dashc: c.args[0],
      preamble: c.args[1] === m.REMOTE_ISOLATE_PREAMBLE,
      dollar0: c.args[2],
      seconds: c.args[3],
      memory: c.args[4],
      inner: c.args.slice(5, 12),
      phase: c.args[12],
      timeout: c.timeout,
      isolateInExec: c.isolate,
    }))));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"cmd":"sh","dashc":"-c","preamble":true,"dollar0":"sh","seconds":"55","memory":"768M","inner":["pr","board","--schema-version","1","--checkout","/w","--phase"],"phase":"listing","timeout":60000,"isolateInExec":false},{"cmd":"sh","dashc":"-c","preamble":true,"dollar0":"sh","seconds":"175","memory":"768M","inner":["pr","board","--schema-version","1","--checkout","/w","--phase"],"phase":"status","timeout":180000,"isolateInExec":false},{"cmd":"sh","dashc":"-c","preamble":true,"dollar0":"sh","seconds":"115","memory":"768M","inner":["pr","board","--schema-version","1","--checkout","/w","--phase"],"phase":"owed","timeout":120000,"isolateInExec":false}]' ]
}

@test "local loadBoard argv is unchanged and has no isolation wrapper" {
  _rr '
    const calls = [];
    const exec = async (cmd, args, opts) => {
      calls.push({ cmd, args: [...args], timeout: opts.timeout });
      return { code: 1, stdout: "", stderr: "skip" };
    };
    await m.loadBoard(exec, { cwd: "/w", on: null }, "listing");
    await m.loadBoard(exec, { cwd: "/w", on: "" }, "status");
    process.stdout.write(JSON.stringify(calls));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"cmd":"pr","args":["board","--schema-version","1","--checkout","/w","--phase","listing"],"timeout":60000},{"cmd":"pr","args":["board","--schema-version","1","--checkout","/w","--phase","status"],"timeout":180000}]' ]
}

@test "toolCall without isolate is unchanged for remote" {
  _rr '
    const a = m.toolCall({ on: "homelab" }, "pr", ["batch", "status", "--full", "--offline"]);
    const b = m.toolCall({ on: "homelab" }, "gh", ["api"]);
    process.stdout.write(JSON.stringify([
      a.cmd, a.args[1] === m.REMOTE_PREAMBLE, a.args[1] === m.REMOTE_ISOLATE_PREAMBLE, a.args.slice(2),
      b.args[1] === m.REMOTE_PREAMBLE,
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["sh",true,false,["sh","pr","batch","status","--full","--offline"],true]' ]
}

@test "classifyCode treats exit 124 as timeout" {
  _rr '
    process.stdout.write(JSON.stringify([
      m.classifyCode({ code: 124, stdout: "", stderr: "timeout: sending signal TERM" }),
      m.classifyCode({ code: 124, stdout: "", stderr: "" }),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"kind":"timeout","message":"timeout: sending signal TERM"},{"kind":"timeout","message":"exit 124"}]' ]
}

_write_echo_stub() {
  local path="$1" name="$2"
  printf '%s\n' '#!/bin/sh' "printf '%s' '${name}'" 'printf '\'' %s'\'' "$@"' 'printf '\''\n'\''' > "$path"
  chmod +x "$path"
}

@test "REMOTE_ISOLATE_PREAMBLE picks systemd-run, timeout, or exec" {
  local preamble bin
  preamble="$(node --input-type=module -e "
    import { REMOTE_ISOLATE_PREAMBLE } from '$REPO_ROOT/ai/herdr/app/mods/_lib/board_data.js';
    process.stdout.write(REMOTE_ISOLATE_PREAMBLE);
  ")"
  [ -n "$preamble" ]
  bin="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bin"
  _write_echo_stub "$bin/systemd-run" systemd-run
  _write_echo_stub "$bin/timeout" timeout
  printf '%s\n' '#!/bin/sh' 'printf '\''payload'\''' 'printf '\'' %s'\'' "$@"' 'printf '\''\n'\''' 'exit 3' > "$bin/payload"
  chmod +x "$bin/payload"

  # systemd-run on PATH but no user bus socket: fall back to a bare timeout.
  mkdir -p "$BATS_TEST_TMPDIR/run"
  run env -u XDG_RUNTIME_DIR PATH="$bin" HOME="$BATS_TEST_TMPDIR" \
    XDG_RUNTIME_DIR="$BATS_TEST_TMPDIR/run" \
    /bin/sh -c "$preamble" sh 55 768M payload a
  [ "$status" -eq 0 ]
  [ "$output" = 'timeout -k 5 55 payload a' ]

  python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' \
    "$BATS_TEST_TMPDIR/run/bus"
  run env -u XDG_RUNTIME_DIR PATH="$bin" HOME="$BATS_TEST_TMPDIR" \
    XDG_RUNTIME_DIR="$BATS_TEST_TMPDIR/run" \
    /bin/sh -c "$preamble" sh 55 768M payload a
  [ "$status" -eq 0 ]
  [ "$output" = 'systemd-run --user --scope --quiet --collect -p MemoryMax=768M -p MemorySwapMax=0 -- timeout -k 5 55 payload a' ]

  rm -f "$bin/systemd-run"
  run env -u XDG_RUNTIME_DIR PATH="$bin" HOME="$BATS_TEST_TMPDIR" \
    /bin/sh -c "$preamble" sh 55 768M payload a
  [ "$status" -eq 0 ]
  [ "$output" = 'timeout -k 5 55 payload a' ]

  rm -f "$bin/timeout"
  run env -u XDG_RUNTIME_DIR PATH="$bin" HOME="$BATS_TEST_TMPDIR" \
    /bin/sh -c "$preamble" sh 55 768M payload a
  [ "$status" -eq 3 ]
  [ "$output" = 'payload a' ]
}
