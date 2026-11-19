#!/usr/bin/env bats
# Tests for the session-brief Pi extension: detect.ts's pure helpers under plain
# node, and wire.ts's handlers driven with a stand-in pi
# (tests/pi_session_brief_fake.mjs).
# select-on: ai/pi/extensions/session-brief/* tests/pi_session_brief_fake.mjs

setup() {
  load 'test_helper'
  common_setup
  # One compile cache for the file: see tests/pi_extensions_issues.bats.
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  EXT="$REPO_ROOT/ai/pi/extensions/session-brief"
}

teardown() {
  common_teardown
}

# _detect BODY — runs BODY under node with m (detect.ts) and out(v) in scope.
# BODY is substituted by bash once; it must not contain dollar-brace or backticks.
_detect() {
  run node --input-type=module -e "
    const m = await import('$EXT/detect.ts');
    const out = (v) => process.stdout.write(JSON.stringify(v));
    $1
  "
}

@test "needsBrief: an assistant message after the last brief needs one; none after does not" {
  _detect '
    const msg = (role) => ({ type: "message", message: { role, content: [{ type: "text", text: "x" }] } });
    const brief = { type: "custom", customType: m.BRIEF_TYPE, data: {} };
    out([m.needsBrief([msg("user"), msg("assistant")]), m.needsBrief([msg("assistant"), brief]),
      m.needsBrief([brief, msg("user")]), m.needsBrief([brief, msg("user"), msg("assistant")]), m.needsBrief([])]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = "[true,false,false,true,false]" ]
}

@test "latestBrief: the data of the last brief entry, or null" {
  _detect '
    const b = (t) => ({ type: "custom", customType: m.BRIEF_TYPE, data: { title_set: t } });
    out([m.latestBrief([b("a"), { type: "message" }, b("b")]),
      m.latestBrief([{ type: "custom", customType: "other", data: {} }])]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"title_set":"b"},null]' ]
}

@test "shouldRename: unnamed, or still the title it set; never a name a person chose" {
  _detect 'out([m.shouldRename(undefined, null), m.shouldRename("", "x"), m.shouldRename("Auto", "Auto"),
    m.shouldRename("Mine", "Auto"), m.shouldRename("Mine", null)])'
  [ "$status" -eq 0 ]
  [ "$output" = "[true,true,true,false,false]" ]
}

@test "transcript: user and assistant text and bash commands, newest kept within the cap" {
  _detect '
    const entries = [
      { type: "message", message: { role: "user", content: "first ask" } },
      { type: "message", message: { role: "assistant", content: [
        { type: "thinking", thinking: "hidden" }, { type: "text", text: "on it" },
        { type: "toolCall", id: "c", name: "bash", arguments: { command: "pr create --draft" } }] } },
      { type: "message", message: { role: "toolResult", toolCallId: "c", content: [{ type: "text", text: "output" }] } },
    ];
    out([m.transcript(entries, 10000), m.transcript(entries, 30)]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["User: first ask\nAssistant: on it\n$ pr create --draft","$ pr create --draft"]' ]
}

@test "parseBrief: title and tldr from the first JSON object, trimmed; anything else is null" {
  _detect '
    out([m.parseBrief("Sure: {\"title\": \"  Fix login race.  \", \"tldr\": \"Fixing it.\"} done"),
      m.parseBrief("{\"title\": \"\", \"tldr\": \"x\"}"), m.parseBrief("no json"),
      m.parseBrief(JSON.stringify({ title: "t".repeat(100), tldr: "x" })).title.length]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"title":"Fix login race","tldr":"Fixing it."},null,null,60]' ]
}

@test "lightTierRef: the light tier from config models output; null when absent or unreadable" {
  _detect '
    out([m.lightTierRef(JSON.stringify({ tiers: { light: { provider: null, id: "claude-haiku-4-5", level: null } } })),
      m.lightTierRef(JSON.stringify({ tiers: { light: { provider: "anthropic", id: "h" } } })),
      m.lightTierRef(JSON.stringify({ tiers: {} })), m.lightTierRef("not json"), m.lightTierRef(null)]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"provider":null,"id":"claude-haiku-4-5"},{"provider":"anthropic","id":"h"},null,null,null]' ]
}

@test "pickModel: provider and id when the tier names one, else that id on the session's provider first" {
  _detect '
    const models = [{ provider: "a", id: "h" }, { provider: "b", id: "h" }, { provider: "b", id: "s" }];
    const registry = { find: (p, i) => models.find((x) => x.provider === p && x.id === i), getAvailable: () => models };
    out([m.pickModel(registry, { provider: "a", id: "h" }, undefined),
      m.pickModel(registry, { provider: null, id: "h" }, { provider: "b" }),
      m.pickModel(registry, { provider: null, id: "h" }, undefined),
      m.pickModel(registry, { provider: null, id: "x" }, undefined) ?? null]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"provider":"a","id":"h"},{"provider":"b","id":"h"},{"provider":"a","id":"h"},null]' ]
}

@test "argv: python3 runs the checkout's ai/bin/pi-sessions for facts, live and clear" {
  _detect '
    const rest = (a) => a.slice(1);
    out([rest(m.factsArgv("/s/f.jsonl")), rest(m.liveArgv(7, "/s/f.jsonl")), rest(m.clearArgv(7)),
      m.factsArgv("x")[0].endsWith("/ai/bin/pi-sessions")]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["facts","/s/f.jsonl","--json"],["live","--pid","7","--session-file","/s/f.jsonl"],["live","--clear","--pid","7"],true]' ]
}

@test "argv: PI_SESSIONS names a file that exists in this checkout" {
  _detect 'const fs = await import("node:fs"); out(fs.existsSync(m.PI_SESSIONS))'
  [ "$status" -eq 0 ]
  [ "$output" = "true" ]
}

@test "shouldBrief: interactive sessions only" {
  _detect 'out(["tui", "rpc", "json", "print"].map(m.shouldBrief))'
  [ "$status" -eq 0 ]
  [ "$output" = "[true,false,false,false]" ]
}

@test "parseFacts: worktrees and prs from pi-sessions facts; empty on anything else" {
  _detect '
    out([m.parseFacts(JSON.stringify({ worktrees: ["/w"], prs: [{ repo: "a/b", number: 1, url: "u", title: "" }, { repo: 3 }] })),
      m.parseFacts("x")]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"worktrees":["/w"],"prs":[{"repo":"a/b","number":1,"url":"u","title":""}]},{"worktrees":[],"prs":[]}]' ]
}

@test "briefData: records spend; omitted spend is empty" {
  _detect '
    const at = new Date("2026-11-17T12:00:00Z");
    const facts = { worktrees: [], prs: [] };
    const withSpend = m.briefData({ titleSet: "t", name: "n", tldr: "d", facts, at,
      spend: [{ label: "opus", cost: 1.5 }] });
    const without = m.briefData({ titleSet: "t", name: "n", tldr: "d", facts, at });
    out([withSpend.spend, without.spend]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[[{"label":"opus","cost":1.5}],[]]' ]
}

@test "briefContext: the transcript goes to the model as one user message under the brief prompt" {
  _detect '
    const c = m.briefContext("User: hi", 5);
    out([c.messages.length, c.messages[0].role, c.messages[0].content[0].text, c.messages[0].timestamp,
      c.systemPrompt.includes("\"title\"")]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[1,"user","Recent turns:\n\nUser: hi",5,true]' ]
}

# _wired BODY — runs BODY under node with wire (wire.ts), m (detect.ts),
# fakePi/ofType/turn (tests/pi_session_brief_fake.mjs) and out(v) in scope.
_wired() {
  run node --input-type=module -e "
    const { wire } = await import('$EXT/wire.ts');
    const m = await import('$EXT/detect.ts');
    const { fakePi, ofType, turn } = await import('$REPO_ROOT/tests/pi_session_brief_fake.mjs');
    const out = (v) => process.stdout.write(JSON.stringify(v));
    $1
  "
}

@test "wire: session_start records the live marker in a tui session only" {
  _wired '
    const f = fakePi(); wire(f.pi, f.deps);
    await f.calls.handlers.session_start({ reason: "startup" }, f.ctx);
    const g = fakePi({ mode: "json" }); wire(g.pi, g.deps);
    await g.calls.handlers.session_start({ reason: "startup" }, g.ctx);
    out([f.calls.execs.map((e) => [e.command].concat(e.args.slice(1))), g.calls.execs.length]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[[["python3","live","--pid","4242","--session-file","/s/x_s1.jsonl"]],0]' ]
}

@test "wire: agent_settled arms a 2-minute timer that input cancels" {
  _wired '
    const f = fakePi({ entries: turn() }); wire(f.pi, f.deps);
    await f.calls.handlers.agent_settled({ aborted: false }, f.ctx);
    await f.calls.handlers.input({ text: "more", source: "interactive" }, f.ctx);
    out(f.calls.timers.map((t) => [t.ms, t.cleared]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[[120000,true]]' ]
}

@test "wire: agent_start also cancels the idle timer" {
  _wired '
    const f = fakePi({ entries: turn() }); wire(f.pi, f.deps);
    await f.calls.handlers.agent_settled({ aborted: false }, f.ctx);
    await f.calls.handlers.agent_start({}, f.ctx);
    out(f.calls.timers.map((t) => t.cleared));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true]' ]
}

@test "wire: the idle timer writes a brief through the light tier and names an unnamed session" {
  _wired '
    const f = fakePi({ entries: turn() }); wire(f.pi, f.deps);
    await f.calls.handlers.agent_settled({ aborted: false }, f.ctx);
    await f.calls.timers[0].fn();
    const [brief] = ofType(f.entries, m.BRIEF_TYPE);
    out([f.state.name, brief.data, f.calls.completes[0].model.id, f.calls.completes[0].options.maxTokens]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["Fix login",{"title_set":"Fix login","name":"Fix login","tldr":"Fixing the login race.","worktrees":["/w/a"],"prs":[],"at":"2026-11-17T12:00:00.000Z","spend":[]},"claude-haiku-4-5",400]' ]
}

@test "wire: spend on the brief is rolled up from entry usage" {
  _wired '
    const entries = [
      { type: "message", message: { role: "user", content: "fix" } },
      { type: "message", message: { role: "assistant", content: [{ type: "text", text: "done" }],
        model: "claude-opus-5-5", usage: { cost: { total: 2.5 } } } },
    ];
    const f = fakePi({ entries }); wire(f.pi, f.deps);
    await f.calls.handlers.agent_settled({ aborted: false }, f.ctx);
    await f.calls.timers[0].fn();
    out(ofType(f.entries, m.BRIEF_TYPE)[0].data.spend);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"label":"opus","cost":2.5}]' ]
}

@test "wire: a name a person chose is kept; the TL;DR still refreshes" {
  _wired '
    const old = { type: "custom", customType: m.BRIEF_TYPE, data: { title_set: "Old auto", tldr: "old" } };
    const f = fakePi({ entries: [...turn(), old, ...turn()], name: "My name" });
    wire(f.pi, f.deps);
    await f.calls.handlers.agent_settled({ aborted: false }, f.ctx);
    await f.calls.timers[0].fn();
    const last = ofType(f.entries, m.BRIEF_TYPE).at(-1).data;
    out([f.state.name, last.title_set, last.name, last.tldr]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["My name","Old auto","My name","Fixing the login race."]' ]
}

@test "wire: an auto title the extension set is regenerated" {
  _wired '
    const old = { type: "custom", customType: m.BRIEF_TYPE, data: { title_set: "Old auto", tldr: "old" } };
    const f = fakePi({ entries: [...turn(), old, ...turn()], name: "Old auto" });
    wire(f.pi, f.deps);
    await f.calls.handlers.agent_settled({ aborted: false }, f.ctx);
    await f.calls.timers[0].fn();
    out([f.state.name, ofType(f.entries, m.BRIEF_TYPE).at(-1).data.title_set]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["Fix login","Fix login"]' ]
}

@test "wire: no new turns since the last brief means no model call" {
  _wired '
    const f = fakePi({ entries: [...turn(), { type: "custom", customType: m.BRIEF_TYPE, data: {} }] });
    wire(f.pi, f.deps);
    await f.calls.handlers.agent_settled({ aborted: false }, f.ctx);
    await f.calls.timers[0].fn();
    out([f.calls.completes.length, ofType(f.entries, m.BRIEF_TYPE).length]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = "[0,1]" ]
}

@test "wire: a failed model call appends nothing and renames nothing" {
  _wired '
    const f = fakePi({ entries: turn(), reply: { stopReason: "error", content: [], errorMessage: "boom" } });
    wire(f.pi, f.deps);
    await f.calls.handlers.agent_settled({ aborted: false }, f.ctx);
    await f.calls.timers[0].fn();
    out([ofType(f.entries, m.BRIEF_TYPE).length, f.state.name ?? null]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = "[0,null]" ]
}

@test "wire: a clean shutdown writes a final brief, then clears the marker; reload writes none" {
  _wired '
    const f = fakePi({ entries: turn() }); wire(f.pi, f.deps);
    await f.calls.handlers.session_shutdown({ reason: "quit" }, f.ctx);
    const g = fakePi({ entries: turn() }); wire(g.pi, g.deps);
    await g.calls.handlers.session_shutdown({ reason: "reload" }, g.ctx);
    out([ofType(f.entries, m.BRIEF_TYPE).length, f.calls.execs.at(-1).args.slice(1),
      ofType(g.entries, m.BRIEF_TYPE).length, g.calls.execs.at(-1).args.slice(1)]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[1,["live","--clear","--pid","4242"],0,["live","--clear","--pid","4242"]]' ]
}

@test "wire: the facts pi-sessions reports are recorded on the brief" {
  _wired '
    const facts = JSON.stringify({ worktrees: ["/w/a", "/w/b"], prs: [{ repo: "acme/widget", number: 7, url: "u", title: "t" }] });
    const f = fakePi({ entries: turn(), facts });
    wire(f.pi, f.deps);
    await f.calls.handlers.session_shutdown({ reason: "quit" }, f.ctx);
    const data = ofType(f.entries, m.BRIEF_TYPE)[0].data;
    out([data.worktrees, data.prs.map((p) => p.number), f.calls.execs[0].args.slice(1)]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["/w/a","/w/b"],[7],["facts","/s/x_s1.jsonl","--json"]]' ]
}

@test "wire: a json-mode session never arms a timer or writes a brief" {
  _wired '
    const f = fakePi({ entries: turn(), mode: "json" }); wire(f.pi, f.deps);
    await f.calls.handlers.agent_settled({ aborted: false }, f.ctx);
    await f.calls.handlers.session_shutdown({ reason: "quit" }, f.ctx);
    out([f.calls.timers.length, f.calls.execs.length, ofType(f.entries, m.BRIEF_TYPE).length]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = "[0,0,0]" ]
}

@test "wire: wire.ts imports the SDK as types only" {
  run grep -nE '^import \{[^}]*\} from "@earendil-works' "$EXT/wire.ts"
  [ "$status" -eq 1 ]
}

@test "index.ts imports only its siblings and SDK types" {
  run grep -nE "^import .*from \"\.\./" "$EXT/index.ts"
  [ "$status" -eq 1 ]
}

@test "wire: a shutdown while an idle brief is in flight waits for it before clearing" {
  _wired '
    let release;
    const gate = new Promise((r) => { release = r; });
    const f = fakePi({ entries: [...turn()], reply: gate });
    wire(f.pi, f.deps);
    await f.calls.handlers.agent_settled({ aborted: false }, f.ctx);
    const idle = f.calls.timers[0].fn();
    let done = false;
    const down = f.calls.handlers.session_shutdown({ reason: "quit" }, f.ctx).then(() => { done = true; });
    for (let i = 0; i < 5; i++) await Promise.resolve();
    const whileInflight = [done, ofType(f.entries, m.BRIEF_TYPE).length, f.calls.execs.some((c) => c.args.includes("--clear"))];
    release({ stopReason: "stop", content: [{ type: "text", text: "{\"title\": \"Fix login\", \"tldr\": \"Fixing the login race.\"}" }] });
    await Promise.all([idle, down]);
    out([whileInflight, done, ofType(f.entries, m.BRIEF_TYPE).length, f.calls.execs.at(-1).args[2]]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[[false,0,false],true,1,"--clear"]' ]
}
