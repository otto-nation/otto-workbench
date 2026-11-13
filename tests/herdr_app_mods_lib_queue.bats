#!/usr/bin/env bats
# Tests for herdr-app otto-queue loaders (exec + ctx, never throw).
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

_q() {
  run node --input-type=module -e "
    import * as q from '$REPO_ROOT/ai/herdr/app/mods/_lib/queue.js';
    import * as d from '$REPO_ROOT/ai/herdr/app/mods/_lib/board_data.js';
    $1
  "
}

@test "statusArgv and cancelArgv" {
  _q 'process.stdout.write(JSON.stringify([q.statusArgv(), q.cancelArgv(12), q.cancelArgv("7")]))'
  [ "$status" -eq 0 ]
  [ "$output" = '[["status","--json"],["cancel","12"],["cancel","7"]]' ]
}

@test "remote toolCall wraps otto-queue with the preamble and is not isolated" {
  _q '
    const calls = [];
    const exec = async (cmd, args, opts) => {
      calls.push({ cmd, args: [...args], cwd: opts && opts.cwd, on: opts && opts.on });
      return { code: 0, stdout: "", stderr: "" };
    };
    await q.loadStatus(exec, { on: "homelab", cwd: null });
    await q.cancelEntry(exec, { on: "homelab", cwd: null }, 3);
    process.stdout.write(JSON.stringify(calls.map((c) => ({
      cmd: c.cmd,
      dashc: c.args[0],
      preamble: c.args[1] === d.REMOTE_PREAMBLE,
      isolate: c.args[1] === d.REMOTE_ISOLATE_PREAMBLE,
      inner: c.args.slice(2),
      cwd: c.cwd,
      on: c.on,
    }))));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"cmd":"sh","dashc":"-c","preamble":true,"isolate":false,"inner":["sh","otto-queue","status","--json"],"cwd":null,"on":"homelab"},{"cmd":"sh","dashc":"-c","preamble":true,"isolate":false,"inner":["sh","otto-queue","cancel","3"],"cwd":null,"on":"homelab"}]' ]
}

@test "parseStatus skips a malformed line and a non-integer id; keeps an unknown state" {
  _q '
    const stdout = [
      "{\"id\":1,\"state\":\"queued\",\"kind\":\"job\",\"entry_class\":\"light\",\"argv\":[\"pr\"]}",
      "not-json",
      "{\"id\":\"x\",\"state\":\"queued\",\"kind\":\"job\"}",
      "{\"id\":2.5,\"state\":\"running\"}",
      "{\"id\":2,\"state\":\"mystery\",\"kind\":\"weird\",\"argv\":[\"echo\"]}",
      "",
    ].join("\n");
    process.stdout.write(JSON.stringify(q.parseStatus(stdout)));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"id":1,"kind":"job","entry_class":"light","idem_key":null,"label":null,"argv":["pr"],"cwd":null,"env_allow":[],"state":"queued","attempts":0,"exit_status":null,"log_path":null,"result":null,"pid":null,"created_at":null,"started_at":null,"finished_at":null,"not_before":null},{"id":2,"kind":"weird","entry_class":null,"idem_key":null,"label":null,"argv":["echo"],"cwd":null,"env_allow":[],"state":"mystery","attempts":0,"exit_status":null,"log_path":null,"result":null,"pid":null,"created_at":null,"started_at":null,"finished_at":null,"not_before":null}]' ]
}

@test "busy (exit 75) returns the soft shape" {
  _q '
    const exec = async () => ({ code: 75, stdout: "", stderr: "" });
    const r = await q.loadStatus(exec, { on: "homelab", cwd: null });
    process.stdout.write(JSON.stringify(r));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":true,"busy":true,"rows":[]}' ]
}

@test "cancel failure surfaces the stderr" {
  _q '
    const exec = async () => ({ code: 1, stdout: "", stderr: "otto-queue: no entry 9" });
    const r = await q.cancelEntry(exec, { on: "box", cwd: null }, 9);
    process.stdout.write(JSON.stringify(r));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"ok":false'* ]]
  [[ "$output" == *'"kind":"failed"'* ]]
  [[ "$output" == *'"otto-queue: no entry 9"'* ]]
}

@test "rowTitle prefers the label, then a readable key, then the command's basename" {
  _q '
    const hex = "a".repeat(64);
    const argv = ["/home/agent/.local/share/otto-workbench/maintenance/bin/otto-workbench-maintenance", "--now"];
    process.stdout.write(JSON.stringify([
      q.rowTitle({ id: 1, label: "pr batch: rebase for 7 PRs", idem_key: "maintenance", argv }),
      q.rowTitle({ id: 2, label: "  ", idem_key: "maintenance", argv }),
      q.rowTitle({ id: 3, idem_key: "review:" + hex, argv }),
      q.rowTitle({ id: 4, idem_key: null, argv: ["sh", "-c", "sleep 2"] }),
      q.rowTitle({ id: 5, argv: [] }),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["pr batch: rebase for 7 PRs","maintenance","otto-workbench-maintenance --now","sh -c sleep 2","#5"]' ]
}

@test "rowTitle does not cut a long command; the view's ellipsis is the only cut" {
  _q '
    const long = "x".repeat(200);
    process.stdout.write(String(q.rowTitle({ id: 1, argv: ["/bin/echo", long] }).length));
  '
  [ "$status" -eq 0 ]
  [ "$output" = "205" ]
}

@test "rowMeta: id, class, key unless it is the title, exit, and retry like otto-queue status" {
  _q '
    const now = 1_000_000_000_000;
    const hex = "b".repeat(64);
    process.stdout.write(JSON.stringify([
      q.rowMeta({ id: 1, entry_class: "heavy", idem_key: "maintenance", state: "done", exit_status: 0 }, now),
      q.rowMeta({ id: 2, entry_class: "heavy", label: "nightly run", idem_key: "nightly", state: "failed", exit_status: 3 }, now),
      q.rowMeta({ id: 3, entry_class: "light", idem_key: "review:" + hex, state: "running" }, now),
      q.rowMeta({ id: 4, entry_class: "heavy", state: "queued", exit_status: 75, attempts: 1, not_before: now / 1000 + 240 }, now),
      q.rowMeta({ id: 5, entry_class: "heavy", state: "queued", attempts: 1, not_before: now / 1000 - 5 }, now),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["#1 · heavy","#2 · heavy · nightly · exit 3","#3 · light · review","#4 · heavy · exit 75 · retry in 4m · attempt 2","#5 · heavy"]' ]
}

@test "parseStatus keeps a label when the ledger carries one" {
  _q '
    const rows = q.parseStatus("{\"id\":1,\"state\":\"done\",\"label\":\"pr batch\"}\n{\"id\":2,\"state\":\"done\"}");
    process.stdout.write(JSON.stringify(rows.map((r) => r.label)));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["pr batch",null]' ]
}

@test "this machine runs otto-queue and the log tail directly, with no remote preamble" {
  _q '
    const calls = [];
    const exec = async (cmd, args, opts) => {
      calls.push({ cmd, args: [...args], on: opts && opts.on });
      return { code: 0, stdout: cmd === "tail" ? "line\n" : "", stderr: "" };
    };
    await q.loadStatus(exec, { on: null, cwd: null });
    const log = await q.loadLog(exec, { on: null, cwd: null }, "/s/queue/logs/4.log");
    process.stdout.write(JSON.stringify({ calls, log }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"calls":[{"cmd":"otto-queue","args":["status","--json"],"on":null},{"cmd":"tail","args":["-n","80","/s/queue/logs/4.log"],"on":null}],"log":{"ok":true,"text":"line\n"}}' ]
}

@test "a remote log tail goes through the preamble and surfaces a failure" {
  _q '
    const calls = [];
    const exec = async (cmd, args, opts) => {
      calls.push({ cmd, inner: args.slice(2), on: opts && opts.on });
      return { code: 1, stdout: "", stderr: "tail: cannot open" };
    };
    const log = await q.loadLog(exec, { on: "homelab", cwd: null }, "/s/queue/logs/9.log");
    process.stdout.write(JSON.stringify({ calls, ok: log.ok, has: /cannot open/.test(log.error.message) }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"calls":[{"cmd":"sh","inner":["sh","tail","-n","80","/s/queue/logs/9.log"],"on":"homelab"}],"ok":false,"has":true}' ]
}
