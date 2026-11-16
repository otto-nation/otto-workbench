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
  _q 'process.stdout.write(JSON.stringify([q.statusArgv(), q.statusArgv({slots:false}), q.cancelArgv(12), q.cancelArgv("7")]))'
  [ "$status" -eq 0 ]
  [ "$output" = '[["status","--json","--slots"],["status","--json"],["cancel","12"],["cancel","7"]]' ]
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
  [ "$output" = '[{"cmd":"sh","dashc":"-c","preamble":true,"isolate":false,"inner":["sh","otto-queue","status","--json","--slots"],"cwd":null,"on":"homelab"},{"cmd":"sh","dashc":"-c","preamble":true,"isolate":false,"inner":["sh","otto-queue","cancel","3"],"cwd":null,"on":"homelab"}]' ]
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
    const parsed = q.parseStatus(stdout);
    process.stdout.write(JSON.stringify({slots: parsed.slots, rows: parsed.rows}));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"slots":null,"rows":[{"id":1,"kind":"job","entry_class":"light","idem_key":null,"label":null,"subject":null,"action":null,"terminal":null,"argv":["pr"],"cwd":null,"env_allow":[],"state":"queued","attempts":0,"exit_status":null,"log_path":null,"result":null,"pid":null,"created_at":null,"started_at":null,"finished_at":null,"not_before":null},{"id":2,"kind":"weird","entry_class":null,"idem_key":null,"label":null,"subject":null,"action":null,"terminal":null,"argv":["echo"],"cwd":null,"env_allow":[],"state":"mystery","attempts":0,"exit_status":null,"log_path":null,"result":null,"pid":null,"created_at":null,"started_at":null,"finished_at":null,"not_before":null}]}' ]
}

@test "busy (exit 75) returns the soft shape" {
  _q '
    const exec = async () => ({ code: 75, stdout: "", stderr: "" });
    const r = await q.loadStatus(exec, { on: "homelab", cwd: null });
    process.stdout.write(JSON.stringify(r));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":true,"busy":true,"rows":[],"slots":null}' ]
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
    const rows = q.parseStatus("{\"id\":1,\"state\":\"done\",\"label\":\"pr batch\"}\n{\"id\":2,\"state\":\"done\"}").rows;
    process.stdout.write(JSON.stringify(rows.map((r) => r.label)));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["pr batch",null]' ]
}

@test "parseStatus keeps subject, action and terminal when the ledger carries them" {
  _q '
    const rows = q.parseStatus("{\"id\":1,\"state\":\"queued\",\"subject\":\"acme/box#7\",\"action\":\"review\",\"terminal\":\"pane-1\"}\n{\"id\":2,\"state\":\"queued\"}").rows;
    process.stdout.write(JSON.stringify(rows.map((r) => [r.subject, r.action, r.terminal])));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["acme/box#7","review","pane-1"],[null,null,null]]' ]
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
  [ "$output" = '{"calls":[{"cmd":"otto-queue","args":["status","--json","--slots"],"on":null},{"cmd":"tail","args":["-n","80","/s/queue/logs/4.log"],"on":null}],"log":{"ok":true,"text":"line\n"}}' ]
}

@test "logNote: ticket, queued job, finished job without a log" {
  _q '
    process.stdout.write(JSON.stringify([
      q.logNote({ kind: "ticket", state: "running", log_path: null }),
      q.logNote({ kind: "job", state: "queued", log_path: null }),
      q.logNote({ kind: "job", state: "done", log_path: null }),
      q.logNote({ kind: "job", state: "done", log_path: "/s/queue/logs/1.log" }),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["Runs in the terminal that started it — no log is kept","Log starts when it runs","No log",null]' ]
}

@test "detailModel: ticket vs job, queued vs running vs done, at a fixed nowMs" {
  _q '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const pick = (row) => {
      const m = q.detailModel(row, NOW);
      return {
        command: m.command,
        facts: m.facts,
        timing: m.timing,
        logNote: m.logNote,
        canFocus: m.canFocus,
        tooltip: m.tooltip === q.timesText(row),
      };
    };
    const ticketQueued = {
      id: 1, kind: "ticket", entry_class: "heavy", argv: ["pr", "review", "--self"],
      cwd: "/work/repo", state: "queued", attempts: 0, created_at: SEC - 120, log_path: null,
    };
    const jobRunning = {
      id: 2, kind: "job", entry_class: "light", argv: ["sleep", "90"], cwd: "/tmp/q",
      state: "running", attempts: 1, pid: 4321, created_at: SEC - 180, started_at: SEC - 72,
      log_path: "/s/queue/logs/2.log",
    };
    const jobDone = {
      id: 3, kind: "job", entry_class: "heavy", argv: ["true"], cwd: "/repo", state: "done",
      attempts: 3, exit_status: 0, pid: 99, created_at: SEC - 64800 - 120,
      started_at: SEC - 64800 - 60, finished_at: SEC - 64800, log_path: null,
    };
    process.stdout.write(JSON.stringify([
      pick(ticketQueued), pick(jobRunning), pick(jobDone),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"command":"pr review --self","facts":[{"key":"Directory","value":"/work/repo","mono":true},{"key":"Kind","value":"Ticket — runs in its terminal"},{"key":"Class","value":"heavy"}],"timing":"waited 2m 0s","logNote":"Runs in the terminal that started it — no log is kept","canFocus":false,"tooltip":true},{"command":"sleep 90","facts":[{"key":"Directory","value":"/tmp/q","mono":true},{"key":"Kind","value":"Job — run by the queue worker"},{"key":"Class","value":"light"},{"key":"PID","value":"4321"}],"timing":"waited 1m 48s · ran 1m 12s","logNote":null,"canFocus":false,"tooltip":true},{"command":"true","facts":[{"key":"Directory","value":"/repo","mono":true},{"key":"Kind","value":"Job — run by the queue worker"},{"key":"Class","value":"heavy"},{"key":"Attempts","value":"3"},{"key":"Exit","value":"0"}],"timing":"waited 1m 0s · ran 1m 0s · ended 18h 0m ago","logNote":"No log","canFocus":false,"tooltip":true}]' ]
}

@test "detailModel omits facts that are absent" {
  _q '
    const m = q.detailModel({ id: 4, argv: ["echo"], state: "queued", attempts: 1 }, 1_700_000_000_000);
    process.stdout.write(JSON.stringify({
      command: m.command, facts: m.facts, timing: m.timing, logNote: m.logNote, canFocus: m.canFocus,
      tooltip: m.tooltip === q.timesText({ id: 4, argv: ["echo"], state: "queued", attempts: 1 }),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"command":"echo","facts":[],"timing":"","logNote":"Log starts when it runs","canFocus":false,"tooltip":true}' ]
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

@test "parseStatus exposes a slots object separately and does not make it a row" {
  _q '
    const stdout = [
      "{\"slots\":{\"heavy\":{\"running\":2,\"limit\":3},\"light\":{\"running\":0,\"limit\":4}}}",
      "{\"id\":1,\"state\":\"running\",\"entry_class\":\"heavy\"}",
      "{\"id\":\"x\"}",
    ].join("\n");
    const parsed = q.parseStatus(stdout);
    process.stdout.write(JSON.stringify({
      slots: parsed.slots,
      host: parsed.host,
      failed24h: parsed.failed24h,
      schema: parsed.schema,
      ids: parsed.rows.map((r) => r.id),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"slots":{"heavy":{"running":2,"limit":3},"light":{"running":0,"limit":4}},"host":null,"failed24h":null,"schema":null,"ids":[1]}' ]
}

@test "parseStatus exposes host, failed24h and schema next to slots" {
  _q '
    const stdout = [
      "{\"slots\":{\"heavy\":{\"running\":1,\"limit\":3}},\"host\":{\"admit_heavy\":false,\"reason\":\"waiting for memory: 0.3 of 1.5 GB\"},\"failed_24h\":25,\"schema\":1}",
      "{\"id\":1,\"state\":\"running\"}",
    ].join("\n");
    const parsed = q.parseStatus(stdout);
    process.stdout.write(JSON.stringify({
      slots: parsed.slots,
      host: parsed.host,
      failed24h: parsed.failed24h,
      schema: parsed.schema,
      ids: parsed.rows.map((r) => r.id),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"slots":{"heavy":{"running":1,"limit":3}},"host":{"admit_heavy":false,"reason":"waiting for memory: 0.3 of 1.5 GB"},"failed24h":25,"schema":1,"ids":[1]}' ]
}

@test "parseStatus treats a null host and failed_24h as null, never an error" {
  _q '
    const parsed = q.parseStatus("{\"slots\":{\"heavy\":{\"running\":0,\"limit\":3}},\"host\":null,\"failed_24h\":null,\"schema\":1}");
    process.stdout.write(JSON.stringify({
      slots: parsed.slots,
      host: parsed.host,
      failed24h: parsed.failed24h,
      schema: parsed.schema,
      error: parsed.error ?? null,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"slots":{"heavy":{"running":0,"limit":3}},"host":null,"failed24h":null,"schema":1,"error":null}' ]
}

@test "loadStatus passes host, failed24h and schema through on a --slots success" {
  _q '
    const exec = async () => ({
      code: 0,
      stdout: ["{\"slots\":{\"heavy\":{\"running\":0,\"limit\":2}},\"host\":{\"admit_heavy\":true,\"reason\":\"host has room\"},\"failed_24h\":0,\"schema\":1}", "{\"id\":4,\"state\":\"queued\"}"].join("\n"),
      stderr: "",
    });
    const r = await q.loadStatus(exec, { on: null, cwd: null });
    process.stdout.write(JSON.stringify({
      ok: r.ok, busy: r.busy, slots: r.slots, host: r.host, failed24h: r.failed24h, schema: r.schema,
      ids: r.rows.map((row) => row.id),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":true,"busy":false,"slots":{"heavy":{"running":0,"limit":2}},"host":{"admit_heavy":true,"reason":"host has room"},"failed24h":0,"schema":1,"ids":[4]}' ]
}

@test "loadStatus retries once without --slots when the old CLI rejects the flag" {
  _q '
    const calls = [];
    const exec = async (cmd, args) => {
      calls.push({ cmd, args: [...args] });
      if (args.includes("--slots")) {
        return {
          code: 2, stdout: "",
          stderr: "otto-queue status: error: unrecognized arguments: --slots",
        };
      }
      return { code: 0, stdout: "{\"id\":1,\"state\":\"queued\"}", stderr: "" };
    };
    const r = await q.loadStatus(exec, { on: null, cwd: null });
    process.stdout.write(JSON.stringify({
      ok: r.ok, busy: r.busy, slots: r.slots, ids: r.rows.map((row) => row.id), calls,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":true,"busy":false,"slots":null,"ids":[1],"calls":[{"cmd":"otto-queue","args":["status","--json","--slots"]},{"cmd":"otto-queue","args":["status","--json"]}]}' ]
}

@test "loadStatus does not retry a non-flag failure" {
  _q '
    const calls = [];
    const exec = async (cmd, args) => {
      calls.push([...args]);
      return { code: 1, stdout: "", stderr: "otto-queue: ledger locked" };
    };
    const r = await q.loadStatus(exec, { on: null, cwd: null });
    process.stdout.write(JSON.stringify({
      ok: r.ok, kind: r.error && r.error.kind, n: calls.length, args: calls[0],
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":false,"kind":"failed","n":1,"args":["status","--json","--slots"]}' ]
}
