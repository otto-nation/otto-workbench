#!/usr/bin/env bats
# Style guards for the herdr-app mods: the brand DESIGN.md rules a grep can see.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_ui/*.js ai/herdr/app/mods/prs/*.js ai/herdr/app/mods/queue/*.js ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  MODS="$REPO_ROOT/ai/herdr/app/mods"
}

teardown() {
  common_teardown
}

@test "no hover callback changes a border colour (single-line match only)" {
  run grep -nE '\.hover\([^)]*\)[^;]*border_color|\.hover\(\(s\) => s[^;]*border_color' \
    "$MODS"/_ui/*.js "$MODS"/prs/*.js "$MODS"/queue/*.js
  [ "$status" -eq 1 ]
}

@test "filter rows go through checkRow; Checkbox.new lives only in _ui" {
  grep -q 'export function checkRow(' "$MODS/_ui/ui.js"
  run grep -n 'Checkbox.new(' "$MODS"/prs/*.js
  [ "$status" -eq 1 ]
}

@test "the brand forbids pills: no pill( call or export remains" {
  run grep -nE '\bpill\(' "$MODS"/_ui/*.js "$MODS"/prs/*.js "$MODS"/queue/*.js
  [ "$status" -eq 1 ]
}

@test "the fail status glyph is ×, never ✗, which no brand font draws" {
  run grep -n '✗' "$MODS"/_ui/*.js "$MODS"/prs/*.js "$MODS"/queue/*.js "$MODS"/_lib/*.js
  [ "$status" -eq 1 ]
  grep -q "fail: Object.freeze({ glyph: '×'" "$MODS/_lib/status.js"
}

@test "load_status indicators are static: no spin, frames, loop, or transition" {
  run grep -nE '\.(spin|frames|loop|transition)\(' "$MODS/prs/load_status.js"
  [ "$status" -eq 1 ]
}

@test "load_status uses SP.sm gap on status text" {
  grep -q 'gap: SP.sm' "$MODS/prs/load_status.js"
}

@test "PR state is read from prPresentation, never re-derived" {
  run grep -nwE 'prIssues|stateTags|ciMark|cardStateLine|toneKind|postKind|healthKind|decisionText' \
    "$MODS"/_ui/*.js "$MODS"/prs/*.js
  [ "$status" -eq 1 ]
}

@test "no inline decision lowercasing or CI/batch wording in views" {
  run grep -nE "toLowerCase\(\)\\.replace\(/_/g|[\"']CI (passing|failing|running|unknown|loading|pending)[\"']|Batch working" \
    "$MODS"/_ui/*.js "$MODS"/prs/*.js
  [ "$status" -eq 1 ]
}

@test "status glyphs live only in _lib/status.js" {
  run grep -n 'glyph:' "$MODS"/_ui/*.js "$MODS"/prs/*.js "$MODS"/queue/*.js
  [ "$status" -eq 1 ]
}

@test "card, compact row and detail header draw every presentation slot" {
  for s in ref title tags author branch stats badges; do
    grep -qE "\\bp\\.$s\\b" "$MODS/prs/board.js"
  done
  for s in ref title tags author row_badges; do
    grep -qE "\\bp\\.$s\\b" "$MODS/prs/compact.js"
  done
  for s in ref title tags author branch base_branch stats detail_issues ci decision review busy; do
    grep -qE "\\bp\\.$s\\b" "$MODS/prs/detail.js"
  done
}

@test "load-error status wording lives only in _lib/load_state.js" {
  run grep -nE 'stale ·|\} failed`' "$MODS"/_ui/*.js "$MODS"/prs/*.js
  [ "$status" -eq 1 ]
}

@test "batch run, step and item status and step labels have one owner in _lib" {
  run grep -nwE 'runKind|stepKind|runWord|RUN_WORD|itemTone|stepTone|STEP_NAMES' "$MODS"/_ui/*.js "$MODS"/prs/*.js "$MODS"/_lib/*.js
  [ "$status" -eq 1 ]
  run grep -lE '^export const (STEP_ORDER|STEP_LABEL|AI_STEPS|RUN_STATUS|STEP_STATUS)\b' "$MODS"/_lib/*.js
  [ "$output" = "$MODS/_lib/batch.js" ]
  run bash -c "grep -nE \"\\[ *['\\\"]rebase['\\\"]\" '$MODS'/_ui/*.js '$MODS'/prs/*.js '$MODS'/_lib/*.js | grep -v '/_lib/batch.js:'"
  [ "$status" -eq 1 ]
}

@test "every pinned header gets its hairline from pinned(), and long text is one selectableCode" {
  grep -q 'pinned(v_flex()' "$MODS/_ui/ui.js"
  grep -q 'pinned(h_flex().gap(SP.md)' "$MODS/prs/batch_page.js"
  grep -q 'pinned(v_flex().id("prs-header")' "$MODS/prs/board_view.js"
  grep -q 'pinned(v_flex().px(SP.md)' "$MODS/prs/board_view.js"
  grep -q 'pinned(h_flex().px(SP.lg).py(SP.md).child(segmented(segs)))' "$MODS/prs/detail.js"
  run grep -ln 'TextView.markdown(`output-\|TextView.markdown(`step-log' "$MODS"/prs/*.js
  [ "$status" -eq 1 ]
  grep -q 'selectableCode("output"' "$MODS/prs/detail.js"
  grep -q 'card(`run-${r.key}`)' "$MODS/prs/detail.js"
  run grep -F 'card(`run-${r.stamp}`)' "$MODS/prs/detail.js"
  [ "$status" -eq 1 ]
  grep -q 'selectableCode(`step-log-' "$MODS/prs/workflows_view.js"
}

@test "board load line is one statusRow used by both the wide and compact headers" {
  [ "$(grep -c 'renderLoadLine(' "$MODS/prs/board_view.js")" -eq 1 ]
  grep -A 6 '^  statusRow(' "$MODS/prs/board_view.js" | grep -q 'renderLoadLine('
  for m in header compactHeader; do
    awk -v m="$m" '$0 ~ "^  "m"\\(" {on=1; next} on && /^  [A-Za-z]+\(/ {exit} on' "$MODS/prs/board_view.js" | grep -q 'this.statusRow'
  done
}
