#!/usr/bin/env bats
# Tests for the run-session Pi extension: the pi session of one `pr batch` run.
# events.ts, now.ts and session.ts import nothing, detect.ts only node builtins,
# and wire.ts SDK types only, so node loads them all directly.
# select-on: ai/pi/extensions/run-session/* ai/pi/extensions/_shared/run-dir.ts

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # The suite may itself run inside a run session, which exports this.
  unset WORKBENCH_RUN_DIR
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  EXT="$REPO_ROOT/ai/pi/extensions/run-session"
  ID="20261114T120000-ab12"
  RUN="$TMPDIR/batch/$ID"
  mkdir -p "$RUN"
}

teardown() {
  common_teardown
}

# _eval FILE EXPR [ARG...] — prints JSON.stringify(EXPR) with $EXT/FILE imported
# as m and the ARGs as args. EXPR is substituted, never re-expanded by bash.
_eval() {
  local file="$1" expr="$2"
  shift 2
  run node --input-type=module -e "
    const m = await import('$EXT/$file');
    const args = process.argv.slice(1);
    process.stdout.write(JSON.stringify($expr));
  " -- "$@"
}

@test "run-session: consume keeps events, counts unreadable lines, keeps unknown kinds" {
  _eval events.ts 'm.consume(Buffer.from(args[0]), 0)' \
    $'{"kind":"a"}\n{torn\n[1]\n{"no":"kind"}\n{"kind":"zzz_new"}\n{"kind":"b"'
  [ "$status" -eq 0 ]
  [ "$output" = '{"events":[{"kind":"a"},{"kind":"zzz_new"}],"skipped":3,"cursor":56}' ]
}

@test "run-session: a half-written last line is read once it is finished" {
  _eval events.ts '(() => { const a = m.consume(Buffer.from(args[0]), 0); return m.consume(Buffer.from(args[0] + args[1]), a.cursor); })()' \
    $'{"kind":"a"}\n{"kind":"b"' $'}\n'
  [ "$status" -eq 0 ]
  [ "$output" = '{"events":[{"kind":"b"}],"skipped":0,"cursor":26}' ]
}

@test "run-session: a cursor past the end re-reads a replaced file from the start" {
  _eval events.ts 'm.consume(Buffer.from(args[0]), 999)' $'{"kind":"a"}\n'
  [ "$status" -eq 0 ]
  [ "$output" = '{"events":[{"kind":"a"}],"skipped":0,"cursor":13}' ]
}

@test "run-session: the cursor counts bytes, not characters" {
  _eval events.ts 'm.consume(Buffer.from(args[0]), 0).cursor === Buffer.byteLength(args[0])' \
    $'{"kind":"\u00e9t\u00e9"}\n'
  [ "$status" -eq 0 ]
  [ "$output" = "true" ]
}

@test "run-session: the latest decision per item and finding wins" {
  _eval events.ts '(() => { const d = m.latestDecisions(JSON.parse(args[0])); return [d.size, d.get(m.decisionKey("o/r#1", "ci_failing:ci")).verdict]; })()' \
    '[{"kind":"decision","item":"o/r#1","finding":"ci_failing:ci","verdict":"deferred"},
      {"kind":"finding_created","item":"o/r#1","reason":"x","step":"y"},
      {"kind":"decision","item":"o/r#1","finding":"ci_failing:ci","verdict":"fixed"},
      {"kind":"decision","item":"o/r#2","finding":"x:y","verdict":"dismissed"},
      {"kind":"decision","finding":"x:y","verdict":"fixed"}]'
  [ "$status" -eq 0 ]
  [ "$output" = '[2,"fixed"]' ]
}

# _text FILE EXPR [ARG...] — prints String(EXPR) with $EXT/FILE imported as m.
_text() {
  local file="$1" expr="$2"
  shift 2
  run node --input-type=module -e "
    const m = await import('$EXT/$file');
    const args = process.argv.slice(1);
    process.stdout.write(String($expr));
  " -- "$@"
}

@test "run-session: history groups run events and PRs and folds a step into one line" {
  _text events.ts 'm.historyText(JSON.parse(args[0]), 0, "T")' '[
    {"schema_version":1,"kind":"run_started","at":"2026-11-14T12:00:00+00:00","run":"r","steps":["rebase","ci"],"pool":2,"with_ai":["ci"],"rebase":"behind"},
    {"kind":"step_started","at":"2026-11-14T12:01:00+00:00","item":"o/r#1","step":"rebase","log_path":"/l/r.log"},
    {"kind":"step_log","at":"2026-11-14T12:01:30+00:00","item":"o/r#1","step":"rebase","line":"noise"},
    {"kind":"step_finished","at":"2026-11-14T12:02:00+00:00","item":"o/r#1","step":"rebase","status":"passed","exit_code":0,"log_path":"/l/r.log"},
    {"kind":"finding_created","at":"2026-11-14T12:03:00+00:00","item":"o/r#1","reason":"ci_failing","step":"ci","detail":"red"},
    {"schema_version":1,"kind":"mystery","at":"2026-11-14T12:04:00+00:00","run":"r","x":1}]'
  [ "$status" -eq 0 ]
  local expected
  expected=$(cat <<'EOF'
T (5 events)
- 12:00 run started: steps rebase,ci · pool 2 · AI steps ci · rebase behind
- 12:04 mystery {"x":1}
o/r#1
  - 12:02 rebase passed (exit 0) log /l/r.log
  - 12:03 finding ci_failing:ci — red
EOF
)
  [ "$output" = "$expected" ]
}

@test "run-session: history names decisions and admission waits" {
  _text events.ts 'm.historyText(JSON.parse(args[0]), 0, "T")' '[
    {"kind":"admission_wait","at":"2026-11-14T12:00:00+00:00","item":"o/r#1","step":"ci","reason":"heavy slots full"},
    {"kind":"decision","at":"2026-11-14T12:01:00+00:00","item":"o/r#1","finding":"ci_failing:ci","verdict":"deferred","reason":"flaky","commands":["pr ci --fix"]},
    {"kind":"item_finished","at":"2026-11-14T12:02:00+00:00","item":"o/r#1","status":"needs_session"}]'
  [ "$status" -eq 0 ]
  local expected
  expected=$(cat <<'EOF'
T (3 events)
o/r#1
  - 12:00 waiting to start ci: heavy slots full
  - 12:01 decided ci_failing:ci: deferred — flaky (ran: pr ci --fix)
  - 12:02 finished: needs_session
EOF
)
  [ "$output" = "$expected" ]
}

@test "run-session: a PR keeps its last HISTORY_CAP lines and folds the rest into a count" {
  _eval events.ts '(() => { const ev = Array.from({ length: m.HISTORY_CAP + 2 }, (_, i) => ({ kind: "item_finished", item: "o/r#2", status: "s" + i })); const lines = m.historyText(ev, 0, "T").split("\n"); return [lines.length, lines[2], lines[3], lines.at(-1)]; })()'
  [ "$status" -eq 0 ]
  [ "$output" = '[15,"  … 2 earlier","  - --:-- finished: s2","  - --:-- finished: s13"]' ]
}

@test "run-session: history says how many lines it skipped, and is empty with nothing to show" {
  _eval events.ts '[m.historyText([], 2, "T"), m.historyText([], 0, "T"), m.historyText([{ kind: "step_log", item: "x" }], 0, "T")]'
  [ "$status" -eq 0 ]
  [ "$output" = '["T (0 events)\n(2 unreadable lines skipped)","",""]' ]
}

@test "run-session: parseReport says what is wrong with a report it cannot use" {
  _eval now.ts '[m.parseReport("nope"), m.parseReport("{}"), m.parseReport("null"), m.parseReport("{\"sessions\":[]}")]'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":false,"error":"the report was not JSON"},{"ok":false,"error":"the report has no sessions list"},{"ok":false,"error":"the report has no sessions list"},{"ok":true,"sessions":[]}]' ]
}

@test "run-session: shellQuote quotes only what a shell would split" {
  _text now.ts 'm.shellQuote(["pr", "ci", "--fix", "o/r#1", "it'"'"'s here", ""])'
  [ "$status" -eq 0 ]
  [ "$output" = "pr ci --fix o/r#1 'it'\\''s here' ''" ]
}

# _atext FILE EXPR [ARG...] — like _text, awaiting EXPR first.
_atext() {
  local file="$1" expr="$2"
  shift 2
  run node --input-type=module -e "
    const m = await import('$EXT/$file');
    const args = process.argv.slice(1);
    process.stdout.write(String(await ($expr)));
  " -- "$@"
}

@test "run-session: Now shows each finding, its latest decision, and the exact commands" {
  _atext now.ts '(async () => { const e = await import(args[2]); const d = e.latestDecisions(JSON.parse(args[1])); return m.renderNow("R1", JSON.parse(args[0]), d); })()' \
    '[{"pr":"o/r#1","state":"needs_session","severity":"block","branch":"b1","base_ref":"main","after":["o/r#2"],
       "findings":[{"reason":"ci_failing","step":"ci","detail":"red","files":["a.py"],"log":"/l/ci.log"},
                   {"reason":"ready_to_push","step":"publish","detail":"","files":[],"log":null}],
       "commands":[{"argv":["pr","ci","--fix","--repo-dir","/w 1"],"why":"fix failing CI","action":"ci"},
                   {"argv":["pr","push","--expect","abc"],"why":"publish","action":"publish","push":true}],
       "notes":["stacked"]}]' \
    '[{"kind":"decision","item":"o/r#1","finding":"ci_failing:ci","verdict":"dismissed","reason":"old"},
      {"kind":"decision","item":"o/r#1","finding":"ci_failing:ci","verdict":"deferred","reason":"flaky runner"}]' \
    "$EXT/events.ts"
  [ "$status" -eq 0 ]
  local expected
  expected=$(cat <<'EOF'
## Now — run R1: 1 of 1 PRs waiting on you

### o/r#1 — needs_session (block) · b1 → main · after o/r#2
- finding `ci_failing:ci` — red (files: a.py) log /l/ci.log
  decided: deferred — flaky runner
- finding `ready_to_push:publish`
- run `pr ci --fix --repo-dir '/w 1'` — fix failing CI
- run `pr push --expect abc` — publish (pushes: only when the operator says so)
- note: stacked
EOF
)
  [ "$output" = "$expected" ]
}

@test "run-session: Now says so when nothing waits" {
  _text now.ts 'm.renderNow("R1", [], new Map())'
  [ "$status" -eq 0 ]
  [ "$output" = $'## Now — run R1\nNothing in the report: no PR has findings or commands.' ]
}

@test "run-session: the latest well-formed state entry on the branch wins" {
  _eval session.ts '[m.latestState(JSON.parse(args[0])), m.latestState([])]' '[
    {"type":"custom","customType":"workbench-run","data":{"runDir":"/a","runId":"a","cursor":3,"nowDigest":"x"}},
    {"type":"message"},
    {"type":"custom","customType":"other","data":{"runDir":"/b","runId":"b","cursor":1}},
    {"type":"custom","customType":"workbench-run","data":{"runDir":"/a","runId":"a","cursor":9}},
    {"type":"custom","customType":"workbench-run","data":{"runDir":"/a","runId":"a","cursor":"12"}}]'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"runDir":"/a","runId":"a","cursor":9,"nowDigest":""},null]' ]
}

@test "run-session: the report argv reads the run offline as JSON" {
  _eval session.ts 'm.reportArgv("R1")'
  [ "$status" -eq 0 ]
  [ "$output" = '["batch","report","R1","--offline","--format","json"]' ]
}

@test "run-session: a decision becomes pr batch note with every flag value attached" {
  _eval session.ts '[m.noteArgv("R1", { pr: "o/r#1", finding: "ci_failing:ci", verdict: "deferred", reason: "-flaky", commands: ["pr ci --fix", "--x"] }), m.noteArgv("R1", { pr: "2", finding: "a:b", verdict: "fixed", reason: "ok" })]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["batch","note","R1","o/r#1","--finding=ci_failing:ci","--verdict=deferred","--reason=-flaky","--command=pr ci --fix","--command=--x"],["batch","note","R1","2","--finding=a:b","--verdict=fixed","--reason=ok"]]' ]
}

@test "run-session: the tool schema offers exactly the verdicts and requires the note's fields" {
  _eval session.ts '[m.DECISION_SCHEMA.properties.verdict.enum, m.VERDICTS, m.DECISION_SCHEMA.required, m.TOOL_NAME]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["fixed","dismissed","deferred","handed_off"],["fixed","dismissed","deferred","handed_off"],["pr","finding","verdict","reason"],"record_decision"]' ]
}

@test "run-session: the system note names the run, the tool, the history file and the push rule" {
  _eval session.ts '(() => { const n = m.systemNote({ runDir: "/s/batch/R1", runId: "R1", cursor: 0, nowDigest: "" }, "/checkout"); return ["R1", "/checkout", "record_decision", "/s/batch/R1/events.jsonl", "Never push", "without --offline"].map((s) => n.includes(s)); })()'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,true,true,true,true,true]' ]
}

@test "run-session: readEvents returns the history, or null for a run without one" {
  printf '{"kind":"a"}\n' > "$RUN/events.jsonl"
  _eval detect.ts '[Buffer.from(m.readEvents(args[0])).toString(), m.readEvents(args[1])]' "$RUN" "$TMPDIR/batch/none"
  [ "$status" -eq 0 ]
  [ "$output" = '["{\"kind\":\"a\"}\n",null]' ]
}

@test "run-session: readEvents throws rather than calling an unreadable history absent" {
  printf 'not a dir\n' > "$TMPDIR/file"
  _eval detect.ts '(() => { try { m.readEvents(args[0]); return "read"; } catch (e) { return e.code; } })()' "$TMPDIR/file"
  [ "$status" -eq 0 ]
  [ "$output" = '"ENOTDIR"' ]
}

@test "run-session: digest is stable sha256 hex" {
  _eval detect.ts '[m.digest("x"), m.digest("x") === m.digest("y")]'
  [ "$status" -eq 0 ]
  [ "$output" = '["2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",false]' ]
}

@test "run-session: pr is the checkout's ai/bin/pr, else pr on PATH" {
  _eval detect.ts '[m.prBinary(), m.prBinary(args[0])]' "$TMPDIR/no-such-pr"
  [ "$status" -eq 0 ]
  # import.meta.url is the module's real path: compare against the physical root.
  [ "$output" = "[\"$(cd "$REPO_ROOT" && pwd -P)/ai/bin/pr\",\"pr\"]" ]
}

@test "run-session: the pure modules import nothing, and detect.ts no SDK" {
  run grep -nE '^import ' "$EXT/session.ts" "$EXT/events.ts"
  [ "$status" -ne 0 ]
  run grep -hE '^import ' "$EXT/now.ts"
  [ "$output" = 'import { decisionKey, type RunEvent } from "./events.ts";' ]
  run grep -E '@earendil-works' "$EXT/detect.ts"
  [ "$status" -ne 0 ]
}

# _event JSON — appends one line to the run's history, as core/run_events.py does.
_event() {
  printf '%s\n' "$1" >> "$RUN/events.jsonl"
}

# _wired BODY — runs BODY under node with wire (wire.ts), m (detect.ts),
# fakePi/ofType (tests/pi_run_session_fake.mjs), fs, RUN, ID and deps(env) in
# scope. BODY is substituted, never re-expanded by bash.
_wired() {
  run node --input-type=module -e "
    const { wire } = await import('$EXT/wire.ts');
    const m = await import('$EXT/detect.ts');
    const { fakePi, ofType } = await import('$REPO_ROOT/tests/pi_run_session_fake.mjs');
    const fs = await import('node:fs');
    const RUN = process.argv[1];
    const ID = m.runIdOf(RUN);
    const deps = (env) => ({ env, block: (text) => ({ text }), pr: 'pr' });
    $1
  " -- "$RUN"
}

@test "run-session: a session that is no run's own is left alone" {
  _wired '
    const f = fakePi({ sessionId: "abc" });
    wire(f.pi, deps({}));
    await f.calls.handlers.session_start({ reason: "startup" }, f.ctx);
    const g = fakePi({ sessionId: "child-1" });
    wire(g.pi, deps({ WORKBENCH_RUN_DIR: RUN }));
    await g.calls.handlers.session_start({ reason: "startup" }, g.ctx);
    console.log(JSON.stringify([f.entries.length, f.calls.messages.length, f.calls.execs.length,
      g.entries.length, g.calls.messages.length, g.calls.execs.length]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = "[0,0,0,0,0,0]" ]
}

@test "run-session: the first open files history, cursor and Now, and starts no turn" {
  _event '{"schema_version":1,"kind":"run_started","at":"2026-11-14T12:00:00+00:00","run":"r","steps":["rebase"],"pool":1,"with_ai":null,"rebase":"conflicts"}'
  _event '{"schema_version":1,"kind":"step_finished","at":"2026-11-14T12:01:00+00:00","run":"r","item":"o/r#1","step":"rebase","status":"passed","exit_code":0,"log_path":"/l/1.log"}'
  _wired '
    const f = fakePi({ sessionId: "batch-" + ID });
    wire(f.pi, deps({ WORKBENCH_RUN_DIR: RUN }));
    await f.calls.handlers.session_start({ reason: "startup" }, f.ctx);
    const states = ofType(f.entries, m.STATE_TYPE);
    console.log(JSON.stringify({
      history: ofType(f.entries, m.HISTORY_TYPE).map((e) => e.data.text.split("\n")),
      states: states.length,
      cursorAtEnd: states[0].data.cursor === fs.statSync(RUN + "/events.jsonl").size,
      message: f.calls.messages.map((x) => [x.message.customType, x.message.display, x.options ?? null]),
      now: f.calls.messages[0].message.content.split("\n")[0],
      exec: f.calls.execs.map((x) => [x.command, ...x.args, x.options.cwd]),
      rendered: f.calls.renderers[m.HISTORY_TYPE]({ data: { text: "hi" } }),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"history":[["Run 20261114T120000-ab12 history (2 events)","- 12:00 run started: steps rebase · pool 1 · AI steps none · rebase conflicts","o/r#1","  - 12:01 rebase passed (exit 0) log /l/1.log"]],"states":1,"cursorAtEnd":true,"message":[["workbench-run-now",true,null]],"now":"## Now — run 20261114T120000-ab12","exec":[["pr","batch","report","20261114T120000-ab12","--offline","--format","json","/checkout"]],"rendered":{"text":"hi"}}' ]
}

@test "run-session: a run with no history file still opens, and says so" {
  _wired '
    const f = fakePi({ sessionId: "batch-" + ID });
    wire(f.pi, deps({ WORKBENCH_RUN_DIR: RUN }));
    await f.calls.handlers.session_start({ reason: "startup" }, f.ctx);
    console.log(JSON.stringify([ofType(f.entries, m.HISTORY_TYPE).map((e) => e.data.text), f.calls.messages.length]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["Run 20261114T120000-ab12: no history recorded (the run predates events.jsonl)."],1]' ]
}

@test "run-session: reopening shows only events past the cursor and an unchanged Now not at all" {
  _event '{"kind":"run_started","at":"2026-11-14T12:00:00+00:00","steps":["rebase"],"pool":1}'
  _wired '
    const entries = [];
    const a = fakePi({ sessionId: "batch-" + ID, entries });
    wire(a.pi, deps({ WORKBENCH_RUN_DIR: RUN }));
    await a.calls.handlers.session_start({ reason: "startup" }, a.ctx);
    fs.appendFileSync(RUN + "/events.jsonl", JSON.stringify({ kind: "item_finished", at: "2026-11-14T12:05:00+00:00", item: "o/r#1", status: "needs_session" }) + "\n");
    const b = fakePi({ sessionId: "elsewhere", entries });
    wire(b.pi, deps({}));
    await b.calls.handlers.session_start({ reason: "resume" }, b.ctx);
    const c = fakePi({ sessionId: "elsewhere", entries });
    wire(c.pi, deps({}));
    await c.calls.handlers.session_start({ reason: "resume" }, c.ctx);
    const texts = ofType(entries, m.HISTORY_TYPE).map((e) => e.data.text);
    console.log(JSON.stringify({ histories: texts.length, latest: texts.at(-1).split("\n"),
      states: ofType(entries, m.STATE_TYPE).length, messages: [a, b, c].map((x) => x.calls.messages.length) }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"histories":2,"latest":["Run 20261114T120000-ab12: new since last look (1 events)","o/r#1","  - 12:05 finished: needs_session"],"states":2,"messages":[1,0,0]}' ]
}

@test "run-session: wire.ts imports the SDK as types only" {
  run grep -nE '^import .*@earendil-works' "$EXT/wire.ts"
  [ "$status" -eq 0 ]
  run grep -nE '^import \{.*@earendil-works' "$EXT/wire.ts"
  [ "$status" -ne 0 ]
}

@test "run-session: before a turn it adds the note, files new history and re-sends a changed Now" {
  _wired '
    const f = fakePi({ sessionId: "batch-" + ID });
    wire(f.pi, deps({ WORKBENCH_RUN_DIR: RUN }));
    await f.calls.handlers.session_start({ reason: "startup" }, f.ctx);
    const event = { systemPromptOptions: { sections: {} } };
    const quiet = await f.calls.handlers.before_agent_start(event, f.ctx);
    fs.appendFileSync(RUN + "/events.jsonl", JSON.stringify({ kind: "decision", at: "2026-11-14T12:06:00+00:00", item: "o/r#1", finding: "ci_failing:ci", verdict: "deferred", reason: "flaky runner", commands: [] }) + "\n");
    f.control.report = JSON.stringify({ sessions: [{ pr: "o/r#1", state: "needs_session", severity: "block", branch: "b1", base_ref: "main", after: [], findings: [{ reason: "ci_failing", step: "ci", detail: "red", files: [], log: null }], commands: [{ argv: ["pr", "ci", "--fix"], why: "fix failing CI", action: "ci" }] }] });
    const changed = await f.calls.handlers.before_agent_start({ systemPromptOptions: { sections: {} } }, f.ctx);
    const note = event.systemPromptOptions.sections[m.SECTION_NAME];
    console.log(JSON.stringify({
      quiet: quiet ?? null,
      note: [note.includes(ID), note.includes("record_decision"), note.includes(RUN + "/events.jsonl")],
      message: [changed.message.customType, changed.message.display],
      now: changed.message.content.split("\n").slice(-3),
      histories: ofType(f.entries, m.HISTORY_TYPE).map((e) => e.data.text.split("\n")[0]),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"quiet":null,"note":[true,true,true],"message":["workbench-run-now",true],"now":["- finding `ci_failing:ci` — red","  decided: deferred — flaky runner","- run `pr ci --fix` — fix failing CI"],"histories":["Run 20261114T120000-ab12: no history recorded (the run predates events.jsonl).","Run 20261114T120000-ab12: new since last look (1 events)"]}' ]
}

@test "run-session: a report that fails says so in Now with the command and its error" {
  _wired '
    const f = fakePi({ sessionId: "batch-" + ID });
    f.control.reportCode = 1;
    wire(f.pi, deps({ WORKBENCH_RUN_DIR: RUN }));
    await f.calls.handlers.session_start({ reason: "startup" }, f.ctx);
    console.log(f.calls.messages[0].message.content);
  '
  [ "$status" -eq 0 ]
  [ "$output" = $'## Now — run 20261114T120000-ab12\nThe report is unavailable: `pr batch report 20261114T120000-ab12 --offline --format json` exited 1: pr batch: no run x. Run it yourself to see why.' ]
}

@test "run-session: before a turn in a session that is no run's own it does nothing" {
  _wired '
    const f = fakePi({ sessionId: "abc" });
    wire(f.pi, deps({}));
    await f.calls.handlers.session_start({ reason: "startup" }, f.ctx);
    const event = { systemPromptOptions: { sections: {} } };
    const out = await f.calls.handlers.before_agent_start(event, f.ctx);
    console.log(JSON.stringify([out ?? null, event.systemPromptOptions.sections, f.calls.execs.length]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[null,{},0]' ]
}
