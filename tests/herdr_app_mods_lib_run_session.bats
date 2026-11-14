#!/usr/bin/env bats
# Tests for herdr-app's Open session decision tree (_lib/run_session.js): ask
# `pr batch session --json`, focus the live labelled terminal, else start one through hc.
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

# Run JS with m = run_session.js, d = board_data.js and out(v) printing JSON.
_rs() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/run_session.js');
    const d = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/board_data.js');
    const out = (v) => process.stdout.write(JSON.stringify(v));
    $1
  "
}

# A stand-in for herdr's exec, run, panes and spaces. answer(line, opts) returns
# {code, stdout, stderr}; line is the command with a remote preamble unwrapped and
# the preamble itself shown as <P>. seen: [{line, on}], ran: [[name, args]].
FAKE='
const seen = []; const ran = [];
const fake = (answer, { panes = [], spaces = [] } = {}) => ({
  exec: async (cmd, args, opts = {}) => {
    let c = cmd, a = args;
    if (c === "sh" && a[2] === "sh") { c = a[3]; a = a.slice(4); }
    const line = [c, ...a].join(" ").replaceAll(d.REMOTE_PREAMBLE, "<P>");
    seen.push({ line, on: opts.on ?? null });
    return answer(line, opts) ?? { code: 1, stdout: "", stderr: "unexpected " + line };
  },
  run: (name, args) => { ran.push([name, args]); return null; },
  panes: () => panes,
  spaces: () => spaces,
});
const RECIPE = JSON.stringify({ schema_version: 1, session_id: "batch-R1", name: "batch o/n R1", cwd: "/w/n", argv: ["pi"], env: {} });
'

@test "sessionArgv and findArgv" {
  _rs 'out([m.sessionArgv("R1"), m.findArgv("batch-R1")])'
  [ "$status" -eq 0 ]
  [ "$output" = '[["batch","session","R1","--json"],["ls","--json","--label","otto.session=batch-R1"]]' ]
}

@test "sessionGate: unknown without features, ready with batch-session, outdated without it" {
  _rs 'out([m.sessionGate(null), m.sessionGate(undefined), m.sessionGate(["review-publish","batch-session"]), m.sessionGate(["review-publish"]), m.sessionGate([])])'
  [ "$status" -eq 0 ]
  [ "$output" = '["unknown","unknown","ready","outdated","outdated"]' ]
}

@test "machineFeatures reads the first PR from that machine, null when none is loaded" {
  _rs 'const prs = [{ on: "homelab", features: ["batch-session"] }, { on: null, features: ["review-publish"] }, { on: "vps" }];
    out([m.machineFeatures(prs, null), m.machineFeatures(prs, "homelab"), m.machineFeatures(prs, "vps"), m.machineFeatures(prs, "box"), m.machineFeatures(null, null)])'
  [ "$status" -eq 0 ]
  [ "$output" = '[["review-publish"],["batch-session"],[],null,null]' ]
}

@test "waitingText counts PRs and says nothing for none" {
  _rs 'out([m.waitingText(1), m.waitingText(3), m.waitingText(0), m.waitingText(null)])'
  [ "$status" -eq 0 ]
  [ "$output" = '["waiting on you: 1 PR","waiting on you: 3 PRs",null,null]' ]
}

@test "parseRecipe needs session_id and cwd" {
  _rs 'out([m.parseRecipe({ session_id: "batch-R1", name: "batch o/n R1", cwd: "/w/n" }), m.parseRecipe({ session_id: "batch-R1" }).ok, m.parseRecipe(null).ok])'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":true,"recipe":{"sessionId":"batch-R1","name":"batch o/n R1","cwd":"/w/n"}},false,false]' ]
}

@test "liveAddress takes a live terminal on the asked machine only" {
  _rs 'const ls = JSON.stringify({ terminals: [
      { address: "homelab/t3", state: "live" }, { address: "t5", state: "finished" }, { address: "t9", state: "live" } ] });
    out([m.liveAddress(ls, null), m.liveAddress(ls, "homelab"), m.liveAddress(ls, "vps"), m.liveAddress("not json", null)])'
  [ "$status" -eq 0 ]
  [ "$output" = '["t9","homelab/t3",null,null]' ]
}

@test "focusSteps selects the pane's space, tab and pane, or keeps a terminal no pane shows" {
  _rs 'const panes = [{ pane: "p4", tab: "tb2", terminal: "t9" }];
    const spaces = [{ id: "sp0", tabs: [{ id: "tb1" }] }, { id: "sp1", tabs: [{ id: "tb2" }] }];
    out([m.focusSteps("t9", panes, spaces), m.focusSteps("t7", panes, spaces)])'
  [ "$status" -eq 0 ]
  [ "$output" = '[[["space.select",{"id":"sp1"}],["tab.select",{"id":"tb2"}],["pane.focus",{"pane":"p4"}]],[["terminal.keep",{"terminal":"t7"}]]]' ]
}
