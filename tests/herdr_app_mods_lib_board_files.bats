#!/usr/bin/env bats
# Tests for herdr-app board_data file loaders: loadFiles, loadFilesIncremental, loadPatch.
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

@test "loadFilesIncremental updates with page 1 before later pages resolve, then all pages in order" {
  _bd '
    function unwrap(cmd, args) {
      if (cmd === "sh" && args[0] === "-c" && args[2] === "sh") return [args[3], args.slice(4)];
      return [cmd, args];
    }
    function stdoutOf(names) {
      return JSON.stringify(names.map((name) => ({filename: name, status: "modified", additions: 1, deletions: 0})));
    }
    const starts = [];
    exec = async (cmd, args) => {
      [cmd, args] = unwrap(cmd, args);
      const rec = { cmd, url: args[1], resolved: false };
      starts.push(rec);
      return await new Promise((resolve) => {
        rec.release = (result) => { rec.resolved = true; resolve(result); };
      });
    };
    async function until(pred) {
      for (let i = 0; i < 200; i++) { if (pred()) return; await Promise.resolve(); }
      throw new Error("until timeout");
    }
    const updates = [];
    const none = { cwd: null, on: null };
    const done = m.loadFilesIncremental(exec, none, "o/n", 7, 250, (u) => {
      updates.push({
        paths: (u.files || []).map((f) => f.path),
        loading: u.loading,
        ok: u.ok,
        starts: starts.length,
        resolved: starts.filter((s) => s.resolved).length,
      });
    });
    await until(() => starts.length >= 1);
    starts[0].release({ code: 0, stdout: stdoutOf(["p1"]), stderr: "" });
    await until(() => updates.length >= 1);
    const first = updates[0];
    await until(() => starts.length >= 3);
    const laterPending = starts.slice(1).every((s) => !s.resolved);
    starts.find((s) => String(s.url).includes("page=3")).release({ code: 0, stdout: stdoutOf(["p3"]), stderr: "" });
    starts.find((s) => String(s.url).includes("page=2")).release({ code: 0, stdout: stdoutOf(["p2"]), stderr: "" });
    await done;
    const last = updates[updates.length - 1];
    process.stdout.write(JSON.stringify({ first, laterPending, lastPaths: last.paths, lastLoading: last.loading }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"first":{"paths":["p1"],"loading":true,"ok":true,"starts":1,"resolved":1},"laterPending":true,"lastPaths":["p1","p2","p3"],"lastLoading":false}' ]
}

@test "loadFilesIncremental requests pages 2..N without waiting for each other" {
  _bd '
    function unwrap(cmd, args) {
      if (cmd === "sh" && args[0] === "-c" && args[2] === "sh") return [args[3], args.slice(4)];
      return [cmd, args];
    }
    function stdoutOf(names) {
      return JSON.stringify(names.map((name) => ({filename: name, status: "modified", additions: 1, deletions: 0})));
    }
    const starts = [];
    exec = async (cmd, args) => {
      [cmd, args] = unwrap(cmd, args);
      const rec = { cmd, url: args[1], resolved: false };
      starts.push(rec);
      return await new Promise((resolve) => {
        rec.release = (result) => { rec.resolved = true; resolve(result); };
      });
    };
    async function until(pred) {
      for (let i = 0; i < 200; i++) { if (pred()) return; await Promise.resolve(); }
      throw new Error("until timeout");
    }
    const none = { cwd: null, on: null };
    const done = m.loadFilesIncremental(exec, none, "o/n", 7, 250, () => {});
    await until(() => starts.length >= 1);
    starts[0].release({ code: 0, stdout: stdoutOf(["p1"]), stderr: "" });
    await until(() => starts.length >= 3);
    const later = starts.slice(1).map((s) => ({ url: s.url, resolved: s.resolved }));
    for (const s of starts) {
      if (!s.resolved) s.release({ code: 0, stdout: stdoutOf(["x"]), stderr: "" });
    }
    await done;
    process.stdout.write(JSON.stringify({
      n: starts.length,
      laterPending: later.every((s) => s.resolved === false),
      pages: later.map((s) => s.url).sort(),
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"n":3'* ]]
  [[ "$output" == *'"laterPending":true'* ]]
  [[ "$output" == *'page=2'* ]]
  [[ "$output" == *'page=3'* ]]
}

@test "loadFilesIncremental keeps page 1 files when page 2 fails" {
  _bd '
    function unwrap(cmd, args) {
      if (cmd === "sh" && args[0] === "-c" && args[2] === "sh") return [args[3], args.slice(4)];
      return [cmd, args];
    }
    function stdoutOf(names) {
      return JSON.stringify(names.map((name) => ({filename: name, status: "modified", additions: 1, deletions: 0})));
    }
    const starts = [];
    exec = async (cmd, args) => {
      [cmd, args] = unwrap(cmd, args);
      const rec = { cmd, url: args[1], resolved: false };
      starts.push(rec);
      return await new Promise((resolve) => {
        rec.release = (result) => { rec.resolved = true; resolve(result); };
      });
    };
    async function until(pred) {
      for (let i = 0; i < 200; i++) { if (pred()) return; await Promise.resolve(); }
      throw new Error("until timeout");
    }
    const updates = [];
    const none = { cwd: null, on: null };
    const done = m.loadFilesIncremental(exec, none, "o/n", 7, 200, (u) => updates.push(u));
    await until(() => starts.length >= 1);
    starts[0].release({ code: 0, stdout: stdoutOf(["p1"]), stderr: "" });
    await until(() => starts.length >= 2);
    starts[1].release({ code: 1, stdout: "", stderr: "page boom" });
    const r = await done;
    process.stdout.write(JSON.stringify({
      ok: r.ok, paths: r.files.map((f) => f.path), loading: r.loading, pageError: r.pageError,
      lastErr: updates[updates.length - 1].pageError,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":true,"paths":["p1"],"loading":false,"pageError":"page boom","lastErr":"page boom"}' ]
}

@test "loadFilesIncremental marks generated in a later update, not the first" {
  _bd '
    function unwrap(cmd, args) {
      if (cmd === "sh" && args[0] === "-c" && args[2] === "sh") return [args[3], args.slice(4)];
      return [cmd, args];
    }
    const starts = [];
    exec = async (cmd, args) => {
      [cmd, args] = unwrap(cmd, args);
      const rec = { cmd, args: [...args], resolved: false };
      starts.push(rec);
      return await new Promise((resolve) => {
        rec.release = (result) => { rec.resolved = true; resolve(result); };
      });
    };
    async function until(pred) {
      for (let i = 0; i < 200; i++) { if (pred()) return; await Promise.resolve(); }
      throw new Error("until timeout");
    }
    const updates = [];
    const done = m.loadFilesIncremental(exec, { cwd: "/repo", on: null }, "o/n", 7, 1, (u) => {
      updates.push({
        generated: (u.files || []).map((f) => f.generated),
        git: starts.some((s) => s.cmd === "git"),
      });
    });
    await until(() => starts.length >= 1);
    starts[0].release({
      code: 0,
      stdout: JSON.stringify([{filename:"a.js",status:"modified",additions:1,deletions:0},{filename:"g.json",status:"modified",additions:1,deletions:0}]),
      stderr: "",
    });
    await until(() => updates.length >= 1);
    const first = updates[0];
    await until(() => starts.some((s) => s.cmd === "git"));
    starts.find((s) => s.cmd === "git").release({
      code: 0,
      stdout: "a.js\0linguist-generated\0unspecified\0g.json\0linguist-generated\0set\0",
      stderr: "",
    });
    await done;
    const last = updates[updates.length - 1];
    process.stdout.write(JSON.stringify({ first, last }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"first":{"generated":[false,false],"git":false},"last":{"generated":[false,true],"git":true}}' ]
}

@test "loadFilesIncremental falls back to loadFiles when total is not a positive integer" {
  _bd '
    const pages = JSON.stringify([{filename:"a.js",status:"modified",additions:1,deletions:0}]) + "\n";
    const table = [{ prefix: "gh api /repos/o/n/pulls/7/files", stdout: pages }];
    const updates = [];
    const r = await m.loadFilesIncremental(exec, { cwd: null, on: null }, "o/n", 7, 1.5, (u) => updates.push(u));
    process.stdout.write(JSON.stringify({
      n: updates.length,
      paginate: calls.some((c) => c.args.includes("--paginate")),
      path: r.files[0].path,
      ok: r.ok,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"n":1,"paginate":true,"path":"a.js","ok":true}' ]
}

@test "loadPatch with a cache issues one exec for two files on the same page" {
  _bd '
    const cache = new Map();
    const table = [{ prefix: "gh api /repos/o/n/pulls/7/files?per_page=100&page=2", stdout: JSON.stringify({"a.js":"@@ a","b.js":"@@ b"}) }];
    const r1 = await m.loadPatch(exec, ctx, "o/n", 7, {path:"a.js", page:2}, cache);
    const r2 = await m.loadPatch(exec, ctx, "o/n", 7, {path:"b.js", page:2}, cache);
    process.stdout.write(JSON.stringify({
      a: r1.patch, b: r2.patch, n: calls.length,
      jq: calls[0].args[calls[0].args.indexOf("--jq") + 1],
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"a":"@@ a","b":"@@ b","n":1,"jq":"map({(.filename): .patch}) | add"}' ]
}

