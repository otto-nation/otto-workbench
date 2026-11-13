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
  [ "$output" = '[{"id":1,"kind":"job","entry_class":"light","idem_key":null,"argv":["pr"],"cwd":null,"env_allow":[],"state":"queued","attempts":0,"exit_status":null,"log_path":null,"result":null,"pid":null,"created_at":null,"started_at":null,"finished_at":null,"not_before":null},{"id":2,"kind":"weird","entry_class":null,"idem_key":null,"argv":["echo"],"cwd":null,"env_allow":[],"state":"mystery","attempts":0,"exit_status":null,"log_path":null,"result":null,"pid":null,"created_at":null,"started_at":null,"finished_at":null,"not_before":null}]' ]
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
