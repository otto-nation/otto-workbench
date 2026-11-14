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
