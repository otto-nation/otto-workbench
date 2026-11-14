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
