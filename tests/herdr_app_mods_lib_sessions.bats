#!/usr/bin/env bats
# Tests for the Sessions panel's model (pure: list argv/parse/load, rows, groups, machines).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/sessions.js ai/herdr/app/mods/_lib/board_data.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# _js BODY — runs BODY with s (sessions.js), out(v), NOW and sess(over) in scope.
# BODY must not contain dollar-brace or backticks: bash substitutes it once.
_js() {
  run node --input-type=module -e "
    import * as s from '$REPO_ROOT/ai/herdr/app/mods/_lib/sessions.js';
    const out = (v) => process.stdout.write(JSON.stringify(v));
    const NOW = Date.parse('2026-11-17T12:00:00Z');
    const sess = (over = {}) => Object.assign({ id: 'abcdef123456', file: '/s/x.jsonl', cwd: '/w/a', started: 't',
      last_active: '2026-11-17T11:00:00Z', name: null, title_set: null, tldr: '', worktrees: [], prs: [], live: null }, over);
    $1
  "
}

@test "listArgv: list --json with the window in days" {
  _js 'out(s.listArgv(9))'
  [ "$status" -eq 0 ]
  [ "$output" = '["list","--json","--since","9"]' ]
}

@test "parseList: schema 1 with a sessions array is ok; anything else is outdated" {
  _js 'out([s.parseList({ schema: 1, sessions: [] }).ok, s.parseList({ schema: 2, sessions: [] }).error.kind,
    s.parseList({ schema: 1 }).error.message])'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,"outdated","Update this machine to list sessions (otto-workbench sync)"]' ]
}

@test "loadSessions: runs pi-sessions and parses; a missing tool reads as outdated" {
  _js '
    const seen = [];
    const ok = async (cmd, args) => { seen.push([cmd].concat(args)); return { code: 0, stdout: JSON.stringify({ schema: 1, sessions: [] }), stderr: "" }; };
    const missing = async () => ({ code: 127, stdout: "", stderr: "pi-sessions: command not found" });
    const garbled = async () => ({ code: 0, stdout: "nope", stderr: "" });
    const a = await s.loadSessions(ok, { on: null, cwd: null }, 18);
    const b = await s.loadSessions(missing, { on: null, cwd: null }, 9);
    const c = await s.loadSessions(garbled, { on: null, cwd: null }, 9);
    out([a.ok, seen[0], b.error.kind, c.error.kind]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,["pi-sessions","list","--json","--since","18"],"outdated","bad-json"]' ]
}

@test "durationText and rssText: minutes, hours, days; MB below a GB" {
  _js 'out([s.durationText(59), s.durationText(3599), s.durationText(7200), s.durationText(172800), s.rssText(300), s.rssText(1536)])'
  [ "$status" -eq 0 ]
  [ "$output" = '["1 min","59 min","2 h","2 d","300 MB","1.5 GB"]' ]
}

@test "titleOf: a person's name, else the auto title, else the folder and short id" {
  _js 'out([s.titleOf(sess({ name: "Mine", title_set: "Auto" })), s.titleOf(sess({ title_set: "Auto" })), s.titleOf(sess())])'
  [ "$status" -eq 0 ]
  [ "$output" = '["Mine","Auto","a · abcdef12"]' ]
}

@test "rowModel: a live close candidate says RSS and idle; a closed row says how long ago" {
  _js '
    const live = s.rowModel(sess({ live: { pid: 7, rss_mb: 1536, idle_s: 64800, terminal: "", close_candidate: true },
      prs: [{ repo: "a/b", number: 7, url: "u", title: "t" }] }), "box", NOW);
    const closed = s.rowModel(sess(), null, NOW);
    out([live.key, live.state, live.candidate, live.pid, live.prs, closed.state, closed.live, closed.key]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["box:abcdef123456","live · 1.5 GB · idle 18 h",true,7,[{"label":"#7","url":"u","title":"t"}],"closed · 1 h ago",false,":abcdef123456"]' ]
}

@test "spendText: labelled costs joined; empty is empty" {
  _js 'out([s.spendText([{ label: "opus", cost: 12.4 }, { label: "grok", cost: 0.01 }]), s.spendText([]), s.spendText(null)])'
  [ "$status" -eq 0 ]
  [ "$output" = '["opus $12.40 · grok $0.01","",""]' ]
}

@test "rowModel: spend appends to the state line; missing spend is unchanged" {
  _js '
    const live = { pid: 7, rss_mb: 1536, idle_s: 64800, terminal: "", close_candidate: true };
    const withSpend = s.rowModel(sess({ live, spend: [{ label: "opus", cost: 12.4 }, { label: "grok", cost: 0.01 }] }), "box", NOW);
    const without = s.rowModel(sess({ live }), "box", NOW);
    const closed = s.rowModel(sess({ spend: [{ label: "opus", cost: 1 }] }), null, NOW);
    out([withSpend.state, without.state, closed.state]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["live · 1.5 GB · idle 18 h · opus $12.40 · grok $0.01","live · 1.5 GB · idle 18 h","closed · 1 h ago · opus $1.00"]' ]
}

@test "groupsOf: grouped by first worktree; close candidates first, then newest" {
  _js '
    const cand = { pid: 1, rss_mb: 900, idle_s: 90000, terminal: "", close_candidate: true };
    const sessions = [
      sess({ id: "new1", worktrees: ["/w/x"], last_active: "2026-11-17T11:30:00Z" }),
      sess({ id: "old1", worktrees: ["/w/x"], last_active: "2026-11-10T00:00:00Z", live: cand }),
      sess({ id: "newest", worktrees: ["/w/y"], last_active: "2026-11-17T11:59:00Z" }),
      sess({ id: "cwdonly", cwd: "/w/z", last_active: "2026-11-01T00:00:00Z" }),
    ];
    out(s.groupsOf(sessions, null, NOW).map((g) => [g.label, g.rows.map((r) => r.id)]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["/w/x",["old1","new1"]],["/w/y",["newest"]],["/w/z",["cwdonly"]]]' ]
}

@test "machineSessions: loading, error with its message, ready with groups" {
  _js '
    const base = { key: "", on: null, title: "This computer", since: 9 };
    const loading = s.machineSessions(Object.assign({}, base, { result: undefined }), NOW);
    const failed = s.machineSessions(Object.assign({}, base, { result: { ok: false, error: { message: "boom" } } }), NOW);
    const ready = s.machineSessions(Object.assign({}, base, { result: { ok: true, doc: { schema: 1, sessions: [sess()] } } }), NOW);
    out([loading.status, failed.status, failed.error, ready.status, ready.groups.length, ready.since]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["loading","error","boom","ready",1,9]' ]
}

@test "folderBasename is the last path segment" {
  _js 'out([s.folderBasename("/w/otto-workbench"), s.folderBasename("/w/otto-workbench/"), s.folderBasename("plain"), s.folderBasename("")])'
  [ "$status" -eq 0 ]
  [ "$output" = '["otto-workbench","otto-workbench","plain",""]' ]
}

@test "machineCountsLine: live and candidates, else session count, else empty" {
  _js '
    const ready = s.machineSessions({ key: "", on: null, title: "This computer", since: 9,
      result: { ok: true, doc: { schema: 1, sessions: [
        sess({ id: "a", live: { pid: 1, rss_mb: 1, idle_s: 1, terminal: "", close_candidate: true } }),
        sess({ id: "b", live: { pid: 2, rss_mb: 1, idle_s: 1, terminal: "", close_candidate: false } }),
        sess({ id: "c" }),
      ] } } }, NOW);
    const closed = s.machineSessions({ key: "", on: null, title: "T", since: 9,
      result: { ok: true, doc: { schema: 1, sessions: [sess()] } } }, NOW);
    const liveOnly = s.machineSessions({ key: "", on: null, title: "T", since: 9,
      result: { ok: true, doc: { schema: 1, sessions: [
        sess({ id: "b", live: { pid: 2, rss_mb: 1, idle_s: 1, terminal: "", close_candidate: false } }),
      ] } } }, NOW);
    const candOnly = s.machineSessions({ key: "", on: null, title: "T", since: 9,
      result: { ok: true, doc: { schema: 1, sessions: [
        sess({ id: "a", live: { pid: 1, rss_mb: 1, idle_s: 1, terminal: "", close_candidate: true } }),
      ] } } }, NOW);
    const loading = s.machineSessions({ key: "", on: null, title: "T", since: 9 }, NOW);
    out([s.machineCountsLine(ready), s.machineCountsLine(closed), s.machineCountsLine(liveOnly), s.machineCountsLine(candOnly), s.machineCountsLine(loading)]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["2 live · 1 close candidate","1 session","1 live","1 live · 1 close candidate",""]' ]
}

@test "isFolderOpen: null open means only a single group starts open" {
  _js 'out([s.isFolderOpen(null, "/w/a", 1), s.isFolderOpen(null, "/w/a", 2), s.isFolderOpen(["/w/a"], "/w/a", 2), s.isFolderOpen([], "/w/a", 1)])'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,false,true,false]' ]
}

@test "ensureOpenState: null plus one key materialises it; two keys stay null; a stored list is kept" {
  _js 'out([s.ensureOpenState(null, ["/w/a"]), s.ensureOpenState(null, ["/w/a","/w/b"]), s.ensureOpenState(["/w/a"], ["/w/a","/w/b"])])'
  [ "$status" -eq 0 ]
  [ "$output" = '[["/w/a"],null,["/w/a"]]' ]
}

@test "toggleFolder materialises the default then flips the key" {
  _js 'out([s.toggleFolder(null, "/w/a", 1, ["/w/a"]), s.toggleFolder(null, "/w/a", 2, ["/w/a","/w/b"]), s.toggleFolder(["/w/a"], "/w/a", 2, ["/w/a","/w/b"])])'
  [ "$status" -eq 0 ]
  [ "$output" = '[[],["/w/a"],[]]' ]
}

@test "homeCards: one card per machine with counts; error gets fail status" {
  _js '
    const ready = s.machineSessions({ key: "", on: null, title: "This computer", since: 9,
      result: { ok: true, doc: { schema: 1, sessions: [sess({ live: { pid: 1, rss_mb: 1, idle_s: 1, terminal: "", close_candidate: false } })] } } }, NOW);
    const failed = s.machineSessions({ key: "h", on: "h", title: "box", since: 9,
      result: { ok: false, error: { message: "boom" } } }, NOW);
    out(s.homeCards([ready, failed]).map((c) => [c.key, c.title, c.counts, c.status ?? null]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["","This computer","1 live",null],["h","box","",{"kind":"fail","word":"error"}]]' ]
}

@test "rowModel: harness is claude only when the row says so; an older pi-sessions row is pi" {
  _js '
    out([s.rowModel(sess({ harness: "claude" }), null, NOW).harness,
         s.rowModel(sess({ harness: "pi" }), null, NOW).harness,
         s.rowModel(sess(), null, NOW).harness,
         s.rowModel(sess({ harness: "other" }), null, NOW).harness]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["claude","pi","pi","pi"]' ]
}
