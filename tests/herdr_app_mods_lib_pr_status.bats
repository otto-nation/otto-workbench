#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/pr_status.js and _lib/status.js: the shared golden fixture, and
# parity of the status kinds, columns and batch step order with their Python owners.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js ai/lib/board/*.py ai/lib/batch/model.py tests/fixtures/board_presentation.json

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  LIB="$REPO_ROOT/ai/herdr/app/mods/_lib"
  FIXTURE="$REPO_ROOT/tests/fixtures/board_presentation.json"
}

teardown() {
  common_teardown
}

# Print JSON.stringify of a JS expression; `m` is pr_status.js, `s` status.js, `c` columns.js, `b` batch.js,
# `fx` the parsed fixture, `canon(v)` v as JSON with object keys sorted.
_js() {
  run node --input-type=module -e "
    const fs = await import('node:fs');
    const m = await import('$LIB/pr_status.js');
    const s = await import('$LIB/status.js');
    const c = await import('$LIB/columns.js');
    const b = await import('$LIB/batch.js');
    const fx = JSON.parse(fs.readFileSync('$FIXTURE', 'utf8'));
    const canon = (v) => JSON.stringify(v, (k, x) => x && typeof x === 'object' && !Array.isArray(x)
      ? Object.fromEntries(Object.keys(x).sort().map((key) => [key, x[key]])) : x);
    process.stdout.write(JSON.stringify($1));
  "
}

# Print JSON of a Python expression over ai/lib.
_py() {
  run python3 -c "
import json, sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import batch.model
import board.columns
import board.model
print(json.dumps($1))
"
}

@test "prPresentation matches every golden case" {
  _js "fx.cases.filter((k) => canon(m.prPresentation({ ...fx.base, ...k.pr }, { item: k.item })) !== canon(k.presentation)).map((k) => k.name)"
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "every golden item's cardState is the batch_state the Python case is given" {
  _js "fx.cases.filter((k) => canon(k.item ? b.cardState(k.item) : null) !== canon(k.batch_state)).map((k) => k.name)"
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "every golden case's lanes are teamColumn and mineColumn" {
  _js "fx.cases.filter((k) => { const pr = { ...fx.base, ...k.pr }; return c.teamColumn(pr) !== k.lanes.team || c.mineColumn(pr) !== k.lanes.mine; }).map((k) => k.name)"
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "the golden cases cover every status kind and badge kind" {
  _js "(() => { const all = fx.cases.flatMap((k) => { const p = k.presentation; return [...p.badges, ...p.detail_issues, p.ci, p.decision, p.busy, p.review].filter(Boolean); });
        return [[...new Set(all.map((x) => x.status).filter(Boolean))].sort(), [...new Set(all.map((x) => x.kind))].sort()]; })()"
  [ "$status" -eq 0 ]
  [ "$output" = '[["fail","none","pass","running","warn"],["status","tag","text"]]' ]
}

@test "status kinds are board.model.StatusKind, in order" {
  _js "s.STATUS_KINDS"
  [ "$status" -eq 0 ]
  js="$output"
  _py "[k.value for k in board.model.StatusKind]"
  [ "$status" -eq 0 ]
  [ "$(jq -c . <<<"$js")" = "$(jq -c . <<<"$output")" ]
}

@test "TEAM_COLUMNS and MINE_COLUMNS are board.columns' keys, labels and order" {
  _js "[...c.TEAM_COLUMNS.map((x, i) => ['team', x.key, x.label, i]), ...c.MINE_COLUMNS.map((x, i) => ['mine', x.key, x.label, i])]"
  [ "$status" -eq 0 ]
  js="$output"
  _py "[[col.board.value, col.key, col.label, col.order] for col in board.columns.columns()]"
  [ "$status" -eq 0 ]
  [ "$(jq -c . <<<"$js")" = "$(jq -c . <<<"$output")" ]
}

@test "STEP_ORDER is batch.model.STEP_ORDER" {
  _js "b.STEP_ORDER"
  [ "$status" -eq 0 ]
  js="$output"
  _py "[step.value for step in batch.model.STEP_ORDER]"
  [ "$status" -eq 0 ]
  [ "$(jq -c . <<<"$js")" = "$(jq -c . <<<"$output")" ]
}

@test "statsText and batchStatus: file noun, no prefix in a run row, null without a card state" {
  _js "[m.statsText({ additions: 1, deletions: 0, files: 1 }), m.statsText({ files: 2 }),
        m.batchStatus({ status: 'queued', steps: {}, findings: [] }), m.batchStatus({ status: 'dropped', steps: {}, findings: [] }), m.batchStatus(null)]"
  [ "$status" -eq 0 ]
  [ "$output" = '[{"additions":"+1","deletions":"-0","files":"1 file"},{"additions":"+0","deletions":"-0","files":"2 files"},{"kind":"status","status":"running","text":"queued"},null,null]' ]
}

@test "status.js: glyphs, unknown kind is none, file marks" {
  _js "[s.GLYPH, s.statusOf('nope') === s.STATUS.none, ['A', 'D', 'M'].map(s.fileMarkKind), s.DIFF]"
  [ "$status" -eq 0 ]
  [ "$output" = '[{"pass":"✓","fail":"×","running":"…","warn":"!","none":"·"},true,["add","del","mod"],{"add":"done","del":"ansi1","mod":"muted"}]' ]
}

@test "RUN_STATUS and STEP_STATUS keys are batch.model RunStatus and StepStatus, in order" {
  _js "[Object.keys(b.RUN_STATUS), Object.keys(b.STEP_STATUS)]"
  [ "$status" -eq 0 ]
  js="$output"
  _py "[[s.value for s in batch.model.RunStatus],[s.value for s in batch.model.StepStatus]]"
  [ "$status" -eq 0 ]
  [ "$(jq -c . <<<"$js")" = "$(jq -c . <<<"$output")" ]
}

@test "itemStatus: batchStatus, else the raw status as none" {
  _js "[m.itemStatus({status:'queued',steps:{},findings:[]}), m.itemStatus({status:'skipped_closed',steps:{},findings:[]})]"
  [ "$status" -eq 0 ]
  [ "$output" = '[{"kind":"status","status":"running","text":"queued"},{"kind":"status","status":"none","text":"skipped closed"}]' ]
}
