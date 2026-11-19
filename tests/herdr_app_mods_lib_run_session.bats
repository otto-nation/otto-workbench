#!/usr/bin/env bats
# Tests for herdr-app's Open session decision tree (_lib/run_session.js): ask
# `pr --branch B target session --json`, focus the live labelled terminal, else
# start one through hc.
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

# A stand-in for herdr's exec, run, panes, spaces, terminals and the spawn's sleep.
# answer(line, opts) returns {code, stdout, stderr}; line is the command with a remote
# preamble unwrapped and the preamble itself shown as <P>. seen: [{line, on}],
# ran: [[name, args]], slept: [ms]. Like herdr, terminals() lists a terminal `hc run`
# started only after the next sleep; `shows: false` never lists it.
FAKE='
const seen = []; const ran = []; const slept = [];
const fake = (answer, { panes = [], spaces = [], terminals = [], shows = true } = {}) => {
  const listed = [...terminals]; let started = [];
  return {
    exec: async (cmd, args, opts = {}) => {
      let c = cmd, a = args;
      if (c === "sh" && a[2] === "sh") { c = a[3]; a = a.slice(4); }
      const line = [c, ...a].join(" ").replaceAll(d.REMOTE_PREAMBLE, "<P>");
      seen.push({ line, on: opts.on ?? null });
      const res = answer(line, opts) ?? { code: 1, stdout: "", stderr: "unexpected " + line };
      const address = line.startsWith("hc run ") && res.code === 0 ? JSON.parse(res.stdout).address : null;
      if (address && shows) started.push({ address });
      return res;
    },
    run: (name, args) => { ran.push([name, args]); return null; },
    panes: () => panes,
    spaces: () => spaces,
    terminals: () => listed,
    sleep: async (ms) => { slept.push(ms); listed.push(...started); started = []; },
  };
};
const TARGET_RECIPE = JSON.stringify({ schema_version: 1, session_id: "target-abc", name: "target o/n", cwd: "/w/x", argv: ["pi"], env: {} });
'

@test "sessionArgv for a target asks pr --branch B target session --json" {
  _rs 'out([m.sessionArgv("target", { branch: "feat/x" }), m.findArgv("target-abc")])'
  [ "$status" -eq 0 ]
  [ "$output" = '[["--branch","feat/x","target","session","--json"],["ls","--json","--label","otto.session=target-abc"]]' ]
}

@test "sessionGate for a target is ready only with target-session" {
  _rs 'out([m.sessionGate(null, "target"), m.sessionGate(undefined, "target"), m.sessionGate(["review-publish","target-session"], "target"), m.sessionGate(["review-publish"], "target"), m.sessionGate([], "target"), m.sessionGate(["batch-session"], "target")])'
  [ "$status" -eq 0 ]
  [ "$output" = '["unknown","unknown","ready","outdated","outdated","outdated"]' ]
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

@test "agentPaneFor: equal and nested cwd match; a sibling does not; a trailing slash is tolerated" {
  _rs 'const eq = { pane: "eq", status: "idle", terminal: "t1", cwd: "/wt/foo" };
    const nested = { pane: "nested", status: "idle", terminal: "t1", cwd: "/wt/foo/sub" };
    const sibling = { pane: "sib", status: "idle", terminal: "t1", cwd: "/wt/foo-2" };
    out([
      m.agentPaneFor([eq], { on: null, cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([nested], { on: null, cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([sibling], { on: null, cwd: "/wt/foo" }),
      m.agentPaneFor([eq], { on: null, cwd: "/wt/foo/" })?.pane,
      m.agentPaneFor([nested], { on: null, cwd: "/wt/foo/" })?.pane,
      m.agentPaneFor([sibling], { on: null, cwd: "/wt/foo/" }),
    ]);'
  [ "$status" -eq 0 ]
  [ "$output" = '["eq","nested",null,"eq","nested",null]' ]
}

@test "agentPaneFor: a pane with no agent or on another machine is ignored" {
  _rs 'const local = { pane: "local", status: "idle", terminal: "t1", cwd: "/wt/foo" };
    const none = { pane: "none", status: null, terminal: "t1", cwd: "/wt/foo" };
    const remote = { pane: "remote", status: "idle", terminal: "homelab/t3", cwd: "/wt/foo" };
    out([
      m.agentPaneFor([none], { on: null, cwd: "/wt/foo" }),
      m.agentPaneFor([none, local], { on: null, cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([remote], { on: null, cwd: "/wt/foo" }),
      m.agentPaneFor([remote], { on: "homelab", cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([local], { on: "homelab", cwd: "/wt/foo" }),
    ]);'
  [ "$status" -eq 0 ]
  [ "$output" = '[null,"local",null,"remote",null]' ]
}

@test "agentPaneFor: blocked beats working beats idle; focused breaks a tie; else first in order" {
  _rs 'const idle = { pane: "idle", status: "idle", terminal: "t1", cwd: "/wt/foo" };
    const working = { pane: "work", status: "working", terminal: "t1", cwd: "/wt/foo" };
    const blocked = { pane: "block", status: "blocked", terminal: "t1", cwd: "/wt/foo" };
    const idleF = { pane: "idleF", status: "idle", terminal: "t1", cwd: "/wt/foo", focused: true };
    const workF = { pane: "workF", status: "working", terminal: "t1", cwd: "/wt/foo", focused: true };
    const idle2 = { pane: "idle2", status: "idle", terminal: "t1", cwd: "/wt/foo" };
    out([
      m.agentPaneFor([idle, working, blocked], { on: null, cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([blocked, working, idle], { on: null, cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([working, idle, blocked], { on: null, cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([working, idle], { on: null, cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([idle, idleF], { on: null, cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([idleF, working], { on: null, cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([working, workF], { on: null, cwd: "/wt/foo" })?.pane,
      m.agentPaneFor([idle, idle2], { on: null, cwd: "/wt/foo" })?.pane,
    ]);'
  [ "$status" -eq 0 ]
  [ "$output" = '["block","block","block","work","idleF","work","workF","idle"]' ]
}

@test "agentPaneFor: branch matches only when cwd is empty" {
  _rs 'const byBranch = { pane: "br", status: "idle", terminal: "t1", cwd: "/other", branch: "feat/x" };
    const byCwd = { pane: "cw", status: "idle", terminal: "t1", cwd: "/wt/foo", branch: "other" };
    out([
      m.agentPaneFor([byBranch], { on: null, cwd: "/wt/foo", branch: "feat/x" }),
      m.agentPaneFor([byCwd], { on: null, cwd: "/wt/foo", branch: "feat/x" })?.pane,
      m.agentPaneFor([byBranch], { on: null, cwd: null, branch: "feat/x" })?.pane,
      m.agentPaneFor([byBranch], { on: null, cwd: "", branch: "feat/x" })?.pane,
      m.agentPaneFor([byCwd], { on: null, cwd: null, branch: "feat/x" }),
    ]);'
  [ "$status" -eq 0 ]
  [ "$output" = '[null,"cw","br","br",null]' ]
}

@test "agentPaneFor: a labelled agent pane with cwd main matches /wt/a" {
  _rs 'const p = { pane: "lab", status: "idle", terminal: "t7", cwd: "main" };
    const terminals = [{ address: "t7", labels: { "otto.worktree": "/wt/a" } }];
    out(m.agentPaneFor([p], { on: null, cwd: "/wt/a", terminals })?.pane);'
  [ "$status" -eq 0 ]
  [ "$output" = '"lab"' ]
}

@test "agentPaneFor: a non-matching label falls through to the cwd tier" {
  _rs 'const p = { pane: "lab", status: "idle", terminal: "t7", cwd: "main" };
    const terminals = [{ address: "t7", labels: { "otto.worktree": "/wt/a" } }];
    out(m.agentPaneFor([p], { on: null, cwd: "main", terminals })?.pane);'
  [ "$status" -eq 0 ]
  [ "$output" = '"lab"' ]
}

@test "agentPaneFor: a labelled agent beats a working blocked unlabelled pane in the same cwd" {
  _rs 'const labelled = { pane: "lab", status: "idle", terminal: "t7", cwd: "main" };
    const blocked = { pane: "block", status: "blocked", terminal: "t8", cwd: "/wt/a" };
    const terminals = [{ address: "t7", labels: { "otto.worktree": "/wt/a" } }];
    out(m.agentPaneFor([blocked, labelled], { on: null, cwd: "/wt/a", terminals })?.pane);'
  [ "$status" -eq 0 ]
  [ "$output" = '"lab"' ]
}

@test "agentPaneFor: shells:false ignores a zsh pane; shells:true returns it only when no agent matches" {
  _rs 'const zsh = { pane: "zsh", status: null, terminal: "t9", cwd: "/wt/a" };
    const agent = { pane: "ag", status: "idle", terminal: "t8", cwd: "/wt/a" };
    out([
      m.agentPaneFor([zsh], { on: null, cwd: "/wt/a" }),
      m.agentPaneFor([zsh], { on: null, cwd: "/wt/a", shells: false }),
      m.agentPaneFor([zsh], { on: null, cwd: "/wt/a", shells: true })?.pane,
      m.agentPaneFor([zsh, agent], { on: null, cwd: "/wt/a", shells: true })?.pane,
    ]);'
  [ "$status" -eq 0 ]
  [ "$output" = '[null,null,"zsh","ag"]' ]
}

@test "agentPaneFor: a worktree label on another machine is ignored" {
  _rs 'const local = { pane: "local", status: "idle", terminal: "t7", cwd: "main" };
    const remote = { pane: "remote", status: "idle", terminal: "homelab/t7", cwd: "main" };
    const remoteTerm = { address: "homelab/t7", labels: { "otto.worktree": "/wt/a" } };
    out([
      m.agentPaneFor([local], { on: null, cwd: "/wt/a", terminals: [remoteTerm] }),
      m.agentPaneFor([remote], { on: null, cwd: "/wt/a", terminals: [remoteTerm] }),
    ]);'
  [ "$status" -eq 0 ]
  [ "$output" = '[null,null]' ]
}

@test "openSession: a remote target asks pr on that machine and starts the terminal there" {
  _rs "$FAKE"'
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 0, stdout: "{\"terminals\":[{\"address\":\"t9\",\"state\":\"live\"}]}", stderr: "" };
      if (line.startsWith("hc run ")) return { code: 0, stdout: "{\"address\":\"homelab/t3\"}", stderr: "" };
    });
    const res = await m.openSession(deps, "target", { on: "homelab", cwd: null, branch: "feat/x", repo: "o/n", features: null });
    out({ res, seen, ran });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":true,"opened":"started"},"seen":[{"line":"pr --branch feat/x target session --json","on":"homelab"},{"line":"hc ls --json --label otto.session=target-abc","on":null},{"line":"hc run --bg --json --cwd /w/x --on homelab --label otto.repo=o/n --label otto.action=session --label otto.app=pr --label otto.session=target-abc -- sh -c <P> sh pr target session","on":null}],"ran":[["terminal.keep",{"terminal":"homelab/t3"}]]}' ]
}

@test "openSession: features without target-session say to update pr and run nothing" {
  _rs "$FAKE"'
    const deps = fake(() => ({ code: 0, stdout: TARGET_RECIPE, stderr: "" }));
    const res = await m.openSession(deps, "target", { on: "homelab", branch: "feat/x", repo: "o/n", features: ["review-publish"] });
    out({ res, seen: seen.length, ran: ran.length });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":false,"outdated":true,"message":"Update pr on this machine to open sessions (otto-workbench sync)"},"seen":0,"ran":0}' ]
}

@test "openSession: a pr without the target subcommand reads as outdated and does not exec further" {
  _rs "$FAKE"'
    const deps = fake(() => ({ code: 2, stdout: "", stderr: "pr: error: argument command: invalid choice: '"'"'target'"'"' (choose from board, batch)" }));
    const res = await m.openSession(deps, "target", { on: null, branch: "feat/x", repo: "o/n", features: null });
    out({ res, seen: seen.length, ran: ran.length });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":false,"outdated":true,"message":"Update pr on this machine to open sessions (otto-workbench sync)"},"seen":1,"ran":0}' ]
}

@test "openSession: pr's own refusal is shown verbatim and nothing starts" {
  _rs "$FAKE"'
    const deps = fake(() => ({ code: 1, stdout: "", stderr: "pr target: branch feat/x is not checked out" }));
    const res = await m.openSession(deps, "target", { on: null, branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, ran: ran.length, hc: seen.filter((s) => s.line.startsWith("hc")).length });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":false,"outdated":false,"message":"pr target: branch feat/x is not checked out"},"ran":0,"hc":0}' ]
}

@test "openSession: a failed lookup refuses rather than start a second pi on the session" {
  _rs "$FAKE"'
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 1, stdout: "", stderr: "core unreachable" };
    });
    const res = await m.openSession(deps, "target", { on: null, branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, hcRun: seen.filter((s) => s.line.startsWith("hc run")).length, ran: ran.length });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":false,"outdated":false,"message":"could not look for an open session: core unreachable"},"hcRun":0,"ran":0}' ]
}

@test "openSession: an hc run failure is reported and no tab is kept" {
  _rs "$FAKE"'
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 0, stdout: "{\"terminals\":[]}", stderr: "" };
      if (line.startsWith("hc run ")) return { code: 1, stdout: "", stderr: "no machine homelab" };
    });
    const res = await m.openSession(deps, "target", { on: null, branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, ran });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":false,"outdated":false,"message":"no machine homelab"},"ran":[]}' ]
}

@test "openSession: a live target session is focused, never started twice" {
  _rs "$FAKE"'
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 0, stdout: "{\"terminals\":[{\"address\":\"t9\",\"state\":\"live\"}]}", stderr: "" };
    }, { panes: [{ pane: "p4", tab: "tb2", terminal: "t9" }], spaces: [{ id: "sp1", tabs: [{ id: "tb2" }] }] });
    const res = await m.openSession(deps, "target", { on: null, branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, hcRun: seen.filter((s) => s.line.startsWith("hc run")).length, ran });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":true,"opened":"focused"},"hcRun":0,"ran":[["space.select",{"id":"sp1"}],["tab.select",{"id":"tb2"}],["pane.focus",{"pane":"p4"}]]}' ]
}

@test "openSession: nothing live starts pr target session in the recipe cwd" {
  _rs "$FAKE"'
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 0, stdout: "{\"terminals\":[]}", stderr: "" };
      if (line.startsWith("hc run ")) return { code: 0, stdout: "{\"address\":\"t9\"}", stderr: "" };
    });
    const res = await m.openSession(deps, "target", { on: null, cwd: "/w/box", branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, seen, ran });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":true,"opened":"started"},"seen":[{"line":"pr --branch feat/x target session --json","on":null},{"line":"hc ls --json --label otto.session=target-abc","on":null},{"line":"hc run --bg --json --cwd /w/x --label otto.repo=o/n --label otto.action=session --label otto.app=pr --label otto.session=target-abc -- pr target session","on":null}],"ran":[["terminal.keep",{"terminal":"t9"}]]}' ]
}

@test "openSession: a labelled live terminal wins over a matching agent pane" {
  _rs "$FAKE"'
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 0, stdout: "{\"terminals\":[{\"address\":\"t9\",\"state\":\"live\"}]}", stderr: "" };
    }, { panes: [
      { pane: "pAgent", tab: "tbAgent", terminal: "tAgent", status: "working", cwd: "/w/x" },
      { pane: "p4", tab: "tb2", terminal: "t9" },
    ], spaces: [
      { id: "spAgent", tabs: [{ id: "tbAgent" }] },
      { id: "sp1", tabs: [{ id: "tb2" }] },
    ] });
    const res = await m.openSession(deps, "target", { on: null, branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, hcRun: seen.filter((s) => s.line.startsWith("hc run")).length, ran });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":true,"opened":"focused"},"hcRun":0,"ran":[["space.select",{"id":"sp1"}],["tab.select",{"id":"tb2"}],["pane.focus",{"pane":"p4"}]]}' ]
}

@test "openSession: a matching agent pane is adopted when no labelled terminal is live" {
  _rs "$FAKE"'
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 0, stdout: "{\"terminals\":[]}", stderr: "" };
      if (line.startsWith("hc run ")) return { code: 0, stdout: "{\"address\":\"t9\"}", stderr: "" };
    }, { panes: [{ pane: "p4", tab: "tb2", terminal: "t8", status: "working", cwd: "/w/x" }], spaces: [{ id: "sp1", tabs: [{ id: "tb2" }] }] });
    const res = await m.openSession(deps, "target", { on: null, branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, hcRun: seen.filter((s) => s.line.startsWith("hc run")).length, seen, ran });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":true,"opened":"adopted"},"hcRun":0,"seen":[{"line":"pr --branch feat/x target session --json","on":null},{"line":"hc ls --json --label otto.session=target-abc","on":null}],"ran":[["space.select",{"id":"sp1"}],["tab.select",{"id":"tb2"}],["pane.focus",{"pane":"p4"}]]}' ]
}

@test "openSession: a labelled agent pane is adopted when the session has no live terminal" {
  _rs "$FAKE"'
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 0, stdout: "{\"terminals\":[]}", stderr: "" };
      if (line.startsWith("hc run ")) return { code: 0, stdout: "{\"address\":\"t9\"}", stderr: "" };
    }, {
      panes: [{ pane: "p4", tab: "tb2", terminal: "t8", status: "working", cwd: "main" }],
      spaces: [{ id: "sp1", tabs: [{ id: "tb2" }] }],
      terminals: [{ address: "t8", labels: { "otto.worktree": "/w/x" } }],
    });
    const res = await m.openSession(deps, "target", { on: null, branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, hcRun: seen.filter((s) => s.line.startsWith("hc run")).length, ran });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":true,"opened":"adopted"},"hcRun":0,"ran":[["space.select",{"id":"sp1"}],["tab.select",{"id":"tb2"}],["pane.focus",{"pane":"p4"}]]}' ]
}

@test "openSession: with no label and no matching pane a target session is started" {
  _rs "$FAKE"'
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 0, stdout: "{\"terminals\":[]}", stderr: "" };
      if (line.startsWith("hc run ")) return { code: 0, stdout: "{\"address\":\"t9\"}", stderr: "" };
    }, { panes: [
      { pane: "none", tab: "tb0", terminal: "t0", status: null, cwd: "/w/x" },
      { pane: "other", tab: "tb1", terminal: "t1", status: "working", cwd: "/w/other" },
    ], spaces: [{ id: "sp1", tabs: [{ id: "tb0" }, { id: "tb1" }] }] });
    const res = await m.openSession(deps, "target", { on: null, cwd: "/w/box", branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, seen, ran });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":true,"opened":"started"},"seen":[{"line":"pr --branch feat/x target session --json","on":null},{"line":"hc ls --json --label otto.session=target-abc","on":null},{"line":"hc run --bg --json --cwd /w/x --label otto.repo=o/n --label otto.action=session --label otto.app=pr --label otto.session=target-abc -- pr target session","on":null}],"ran":[["terminal.keep",{"terminal":"t9"}]]}' ]
}

@test "openSession: a started session is kept only once herdr lists its terminal" {
  _rs "$FAKE"'
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 0, stdout: "{\"terminals\":[]}", stderr: "" };
      if (line.startsWith("hc run ")) return { code: 0, stdout: "{\"address\":\"t9\"}", stderr: "" };
    });
    const run = deps.run;
    deps.run = (name, args) => { run(name, args); ran.push(["listed", deps.terminals().map((t) => t.address)]); };
    const res = await m.openSession(deps, "target", { on: null, cwd: "/w/box", branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, slept, ran });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":true,"opened":"started"},"slept":[100],"ran":[["terminal.keep",{"terminal":"t9"}],["listed",["t9"]]]}' ]
}

@test "openSession: a started session herdr never lists is an error that names it, with no keep" {
  _rs "$FAKE"'
    const l = await import("'"$REPO_ROOT"'/ai/herdr/app/mods/_lib/launch.js");
    const deps = fake((line) => {
      if (line === "pr --branch feat/x target session --json") return { code: 0, stdout: TARGET_RECIPE, stderr: "" };
      if (line.startsWith("hc ls ")) return { code: 0, stdout: "{\"terminals\":[]}", stderr: "" };
      if (line.startsWith("hc run ")) return { code: 0, stdout: "{\"address\":\"t9\"}", stderr: "" };
    }, { shows: false });
    const res = await m.openSession(deps, "target", { on: null, cwd: "/w/box", branch: "feat/x", repo: "o/n", features: ["target-session"] });
    out({ res, waited: slept.reduce((a, b) => a + b, 0) === l.KEEP_WAIT_MS, ran });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"res":{"ok":false,"outdated":false,"message":"started t9, but herdr did not show it within 3s (hc attach t9)"},"waited":true,"ran":[]}' ]
}

@test "run_session.js string literals name batch and target only inside SESSION_KINDS" {
  _rs '
    const fs = await import("node:fs");
    let src = fs.readFileSync("'"$REPO_ROOT"'/ai/herdr/app/mods/_lib/run_session.js", "utf8");
    src = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    const start = src.indexOf("export const SESSION_KINDS");
    const end = src.indexOf("};", start) + 2;
    const rest = src.slice(0, start) + src.slice(end);
    const hits = [...rest.matchAll(/["'\'']([^"'\'']*)["'\'']/g)].map((m) => m[1]).filter((s) => /\b(batch|target)\b/.test(s));
    out(hits);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}
