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
      // Remote calls are wrapped to load the workbench env; isolate inserts seconds, MemoryMax, scope|plain.
      // The table speaks of the inner command.
      if (cmd === 'sh' && args[0] === '-c' && args[2] === 'sh') {
        if (args[1] === m.REMOTE_ISOLATE_PREAMBLE) [cmd, args] = [args[6], args.slice(7)];
        else if (args[1] === m.REMOTE_PREAMBLE) [cmd, args] = [args[3], args.slice(4)];
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

@test "gh auth login stderr is kind auth" {
  _bd '
    const table = [{ prefix: "gh api /repos/acme/box/pulls/7/files", code: 1, stderr: "gh auth login required" }];
    const r = pack(await m.loadFiles(exec, ctx, "acme/box", 7));
    process.stdout.write(JSON.stringify({ r, every: calls.every(everyOn) }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"auth"'* ]]
  [[ "$output" == *'"every":true'* ]]
}

@test "exec reject with on set is exec with the command; on null ENOENT is missing-tool" {
  _bd '
    let table = [{ prefix: "gh api /repos/acme/box/pulls/7/files", reject: { message: "`herdr.exec`: hc exec: " } }];
    const remote = pack(await m.loadFiles(exec, ctx, "acme/box", 7));
    calls.length = 0;
    const miss = pack(await m.loadFiles(exec, { cwd: "/repo", on: null }, "acme/box", 7));
    process.stdout.write(JSON.stringify({
      remote, miss, n: calls.length, onNull: calls[0] && calls[0].on === null,
      cwdOk: calls[0] && calls[0].cwd === "/repo",
    }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"exec"'* ]]
  [[ "$output" == *'"command":"gh api /repos/acme/box/pulls/7/files?per_page=100"'* ]]
  [[ "$output" == *'"message":""'* ]]
  [[ "$output" == *'"kind":"missing-tool"'* ]]
  [[ "$output" == *'"onNull":true'* ]]
}

@test "a remote exec timeout is kind timeout" {
  _bd '
    const table = [{ prefix: "gh api /repos/acme/box/pulls/7/files", reject: { message: "`herdr.exec`: gh took longer than 30 s on mac and was stopped" } }];
    const r = await m.loadFiles(exec, ctx, "acme/box", 7);
    process.stdout.write(JSON.stringify(r));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"timeout"'* ]]
  [[ "$output" == *'"message":"gh took longer than 30 s on mac and was stopped"'* ]]
}

@test "bad JSON is kind bad-json" {
  _bd '
    const table = [{ prefix: "pr board --schema-version 1", stdout: "not-json" }];
    const r = pack(await m.loadBoard(exec, ctx, "listing"));
    process.stdout.write(JSON.stringify({ r, every: calls.every(everyOn) }));
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *'"kind":"bad-json"'* ]]
  [[ "$output" == *'"every":true'* ]]
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

@test "toolCall leaves local calls alone and wraps remote ones in the workbench env preamble" {
  _bd '
    process.stdout.write(JSON.stringify([
      m.toolCall({ on: null }, "pr", ["review", "--list"]),
      m.toolCall({ on: "" }, "gh", ["api"]),
      m.toolCall({ on: "homelab" }, "pr", ["review", "--list"]),
      m.REMOTE_PREAMBLE.endsWith("exec \"$@\""),
    ]).replaceAll(JSON.stringify(m.REMOTE_PREAMBLE), "\"<PREAMBLE>\""));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"cmd":"pr","args":["review","--list"]},{"cmd":"gh","args":["api"]},{"cmd":"sh","args":["-c","<PREAMBLE>","sh","pr","review","--list"]},true]' ]
}


@test "a remote pr that is coreutils' pr is named as the wrong pr, not shown as raw usage" {
  _bd '
    const table = [{ prefix: "pr board", code: 1, stderr: "pr: unrecognized option \x27--board\x27\nTry \x27pr --help\x27 for more information." }];
    const r = await m.loadBoard(exec, { cwd: "/w", on: "homelab" }, "listing");
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
      mode: c.args[5],
      inner: c.args.slice(6, 13),
      phase: c.args[13],
      timeout: c.timeout,
      isolateInExec: c.isolate,
    }))));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"cmd":"sh","dashc":"-c","preamble":true,"dollar0":"sh","seconds":"55","memory":"768M","mode":"scope","inner":["pr","board","--schema-version","1","--checkout","/w","--phase"],"phase":"listing","timeout":60000,"isolateInExec":false},{"cmd":"sh","dashc":"-c","preamble":true,"dollar0":"sh","seconds":"175","memory":"768M","mode":"scope","inner":["pr","board","--schema-version","1","--checkout","/w","--phase"],"phase":"status","timeout":180000,"isolateInExec":false},{"cmd":"sh","dashc":"-c","preamble":true,"dollar0":"sh","seconds":"115","memory":"768M","mode":"scope","inner":["pr","board","--schema-version","1","--checkout","/w","--phase"],"phase":"owed","timeout":120000,"isolateInExec":false}]' ]
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

@test "isolated remote call retries under plain timeout when systemd-run itself fails" {
  _rr '
    const modes = [];
    const exec = async (cmd, args) => {
      modes.push(args[5]);
      if (args[5] === "scope") {
        return { code: 1, stdout: "", stderr: "Failed to start transient scope unit: Access denied\n" };
      }
      return { code: 0, stdout: "{}", stderr: "" };
    };
    const r = await m.invoke(exec, { cwd: "/w", on: "box" }, "pr", ["board"],
      { timeout: 60000, isolate: { seconds: 55, memoryMax: "768M" } });
    process.stdout.write(JSON.stringify({ modes, ok: r.ok }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"modes":["scope","plain"],"ok":true}' ]
}

@test "isolated remote call does not retry when the command itself fails" {
  _rr '
    let calls = 0;
    const exec = async () => { calls += 1; return { code: 1, stdout: "", stderr: "pr board: boom" }; };
    const r = await m.invoke(exec, { cwd: "/w", on: "box" }, "pr", ["board"],
      { isolate: { seconds: 55, memoryMax: "768M" } });
    process.stdout.write(JSON.stringify({ calls, kind: r.error.kind }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"calls":1,"kind":"failed"}' ]
}

@test "isolated remote exit 137 names the memory cap and deadline" {
  _rr '
    const exec = async () => ({ code: 137, stdout: "", stderr: "" });
    const r = await m.invoke(exec, { cwd: "/w", on: "box" }, "pr", ["board"],
      { isolate: { seconds: 55, memoryMax: "768M" } });
    process.stdout.write(JSON.stringify(r.error));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"kind":"failed","message":"killed at its 768M memory cap or 55s deadline"}' ]
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
  # The environment the preamble loads first is not this test's subject, and the
  # real one would put the host's Homebrew dir (and any timeout in it) on PATH.
  mkdir -p "$BATS_TEST_TMPDIR/.config/zsh"
  printf 'workbench_env() { :; }\n' > "$BATS_TEST_TMPDIR/.config/zsh/env.sh"
  _write_echo_stub "$bin/systemd-run" systemd-run
  _write_echo_stub "$bin/timeout" timeout
  printf '%s\n' '#!/bin/sh' 'printf '\''payload'\''' 'printf '\'' %s'\'' "$@"' 'printf '\''\n'\''' 'exit 3' > "$bin/payload"
  chmod +x "$bin/payload"

  # systemd-run on PATH but no user bus socket: fall back to a bare timeout.
  mkdir -p "$BATS_TEST_TMPDIR/run"
  run env -u XDG_RUNTIME_DIR PATH="$bin" HOME="$BATS_TEST_TMPDIR" \
    XDG_RUNTIME_DIR="$BATS_TEST_TMPDIR/run" \
    /bin/sh -c "$preamble" sh 55 768M scope payload a
  [ "$status" -eq 0 ]
  [ "$output" = 'timeout -k 5 55 payload a' ]

  python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' \
    "$BATS_TEST_TMPDIR/run/bus"
  run env -u XDG_RUNTIME_DIR PATH="$bin" HOME="$BATS_TEST_TMPDIR" \
    XDG_RUNTIME_DIR="$BATS_TEST_TMPDIR/run" \
    /bin/sh -c "$preamble" sh 55 768M scope payload a
  [ "$status" -eq 0 ]
  [ "$output" = 'systemd-run --user --scope --quiet --collect -p MemoryMax=768M -p MemorySwapMax=0 -- timeout -k 5 55 payload a' ]

  # plain mode skips systemd-run even when it and the bus are usable.
  run env -u XDG_RUNTIME_DIR PATH="$bin" HOME="$BATS_TEST_TMPDIR" \
    XDG_RUNTIME_DIR="$BATS_TEST_TMPDIR/run" \
    /bin/sh -c "$preamble" sh 55 768M plain payload a
  [ "$status" -eq 0 ]
  [ "$output" = 'timeout -k 5 55 payload a' ]

  rm -f "$bin/systemd-run"
  run env -u XDG_RUNTIME_DIR PATH="$bin" HOME="$BATS_TEST_TMPDIR" \
    /bin/sh -c "$preamble" sh 55 768M scope payload a
  [ "$status" -eq 0 ]
  [ "$output" = 'timeout -k 5 55 payload a' ]

  rm -f "$bin/timeout"
  run env -u XDG_RUNTIME_DIR PATH="$bin" HOME="$BATS_TEST_TMPDIR" \
    /bin/sh -c "$preamble" sh 55 768M scope payload a
  [ "$status" -eq 3 ]
  [ "$output" = 'payload a' ]
}

# A fake remote HOME: the real env.sh deployed where the preamble looks, a mise
# shim for wt, and an ~/.env.local.
_remote_home() {
  local home="$BATS_TEST_TMPDIR/remote"
  mkdir -p "$home/.config/zsh" "$home/.local/share/mise/shims" "$home/.local/bin"
  cp "$REPO_ROOT/zsh/env.sh" "$home/.config/zsh/env.sh"
  printf '#!/bin/sh\necho wt-shim\n' > "$home/.local/share/mise/shims/wt"
  chmod +x "$home/.local/share/mise/shims/wt"
  printf 'GOOGLE_CLOUD_PROJECT=from-env-local\n' > "$home/.env.local"
  printf '%s' "$home"
}

_preamble() {
  node --input-type=module -e "
    import { REMOTE_PREAMBLE } from '$REPO_ROOT/ai/herdr/app/mods/_lib/board_data.js';
    process.stdout.write(REMOTE_PREAMBLE);
  "
}

@test "REMOTE_PREAMBLE reaches mise shims and exports ~/.env.local from the deployed env.sh" {
  local home preamble
  home="$(_remote_home)"
  preamble="$(_preamble)"

  run env -i HOME="$home" PATH=/usr/bin:/bin /bin/sh -c "$preamble" sh \
    /bin/sh -c 'wt; printf "%s\n" "$GOOGLE_CLOUD_PROJECT"; printf "%s\n" "${PATH%%:*}"'

  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "wt-shim" ]
  [ "${lines[1]}" = "from-env-local" ]
  [ "${lines[2]}" = "$home/.local/bin" ]
}

@test "REMOTE_PREAMBLE exits 127 naming the fix when env.sh is not deployed" {
  local home preamble
  home="$(_remote_home)"
  rm "$home/.config/zsh/env.sh"
  preamble="$(_preamble)"

  run -127 --separate-stderr env -i HOME="$home" PATH=/usr/bin:/bin /bin/sh -c "$preamble" sh /bin/echo ran

  [ "$status" -eq 127 ]
  [ -z "$output" ]
  [[ "$stderr" == *"/.config/zsh/env.sh) is missing on this machine"*"otto-workbench sync"* ]]
}
