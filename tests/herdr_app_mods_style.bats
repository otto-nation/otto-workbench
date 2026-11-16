#!/usr/bin/env bats
# Style guards for the herdr-app mods: the brand DESIGN.md rules a grep can see.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_ui/*.js ai/herdr/app/mods/prs/*.js ai/herdr/app/mods/queue/*.js ai/herdr/app/mods/queue-button/*.js ai/herdr/app/mods/_lib/*.js

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
    "$MODS"/_ui/*.js "$MODS"/prs/*.js "$MODS"/queue/*.js "$MODS"/_lib/panel_button.js
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
  run grep -nE '\.(spin|frames|loop|transition)\(' "$MODS/prs/detail.js"
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

@test "mods never use TextView.scrollable: a self-scrolling TextView lays out at zero in an auto-height parent" {
  run grep -rnF --include='*.js' '.scrollable(' "$MODS"
  [ "$status" -eq 1 ]
}

@test "mods never call setTimeout, clearTimeout, setInterval, or clearInterval" {
  run grep -rnE --include='*.js' '\b(setTimeout|clearTimeout|setInterval|clearInterval)\s*\(' "$MODS"
  [ "$status" -eq 1 ]
}

@test "PR search is a Search PRs input at the start of the filter bar" {
  grep -q 'placeholder: "Search PRs"' "$MODS/prs/filter_bar.js"
  grep -q 'id("prs-query")' "$MODS/prs/filter_bar.js"
  awk '/return h_flex\(\)\.id\("prs-filters"\)/,/\.children\(\(model\.facets/' "$MODS/prs/filter_bar.js" | grep -q 'this.queryField'
}

@test "board load line is one statusRow used by both the wide and compact headers" {
  [ "$(grep -c 'renderLoadLine(' "$MODS/prs/board_view.js")" -eq 1 ]
  grep -A 6 '^  statusRow(' "$MODS/prs/board_view.js" | grep -q 'renderLoadLine('
  for m in header compactHeader; do
    awk -v m="$m" '$0 ~ "^  "m"\\(" {on=1; next} on && /^  [A-Za-z]+\(/ {exit} on' "$MODS/prs/board_view.js" | grep -q 'this.statusRow'
  done
}

@test "queue view draws lanes, filter and staleness from the model" {
  local view="$MODS/queue/view.js"
  grep -q 'laneTitle(lane.label, lane.count)' "$view"
  grep -q 'sectionLabel(summary.title)' "$view"
  grep -Fq 'segmented([' "$view"
  grep -q 'segment("queue-filter-all", "All"' "$view"
  grep -q 'segment("queue-filter-live", "Live"' "$view"
  grep -q 'segment("queue-filter-failed", "Failed"' "$view"
  grep -q 'caption(props.summary)' "$view"
  grep -q 'spinning: props.spinning' "$view"
  grep -q 'statusText("warn", stale.words)' "$view"
  grep -q 'if (!row.members) return renderOne(on, row, opts);' "$view"
  grep -q 'row.members.flatMap' "$view"
  grep -q 'row.timing' "$view"
  grep -q 'row.position' "$view"
  grep -q 'row.retry' "$view"
  grep -q 'row.failure' "$view"
  grep -q 'onToggleGroup' "$view"
  run grep -nE 'filterRows\(|lanesModel\(|staleness\(|refreshSpinning\(|liveCounts\(|rowMeta\(|ageText\(' "$view"
  [ "$status" -eq 1 ]
  run grep -nF 'spinning: props.manual' "$view"
  [ "$status" -eq 1 ]
}

@test "queue main composes lanes, filter, staleness and refreshSpinning" {
  local main="$MODS/queue/main.js"
  grep -q 'lanesModel(filterRows(rows, this.filter), nowMs)' "$main"
  grep -q 'staleness({ result, at: entry.at }, nowMs)' "$main"
  grep -q 'refreshSpinning(this.refreshSince, nowMs)' "$main"
  grep -q 'summary: summary(allRows)' "$main"
  grep -q 'onFilter: (filter) => this.setFilter(filter)' "$main"
  grep -q 'onToggleGroup: (on, id) => this.toggleGroup(on, id)' "$main"
}

@test "queue home draws homeModel blocks with gauges, counts and a collapse chevron" {
  local view="$MODS/queue/view.js"
  local main="$MODS/queue/main.js"
  grep -q 'homeModel(' "$main"
  grep -q 'toggleCollapsed(' "$main"
  grep -q 'shared("queue.collapsed")' "$main"
  grep -q 'setShared("queue.collapsed"' "$main"
  grep -q 'sectionLabel(summary.title)' "$view"
  grep -q 'gauge(g.text' "$view"
  grep -q 'caption(summary.counts)' "$view"
  grep -q 'block.open ? "▾" : "▸"' "$view"
  grep -q 'onToggleMachine' "$view"
  grep -q 'opts.onToggleMachine(block.key, block.open)' "$view"
  grep -q 'props.blocks' "$view"
  run grep -nE 'homeModel|toggleCollapsed|slotGauge' "$view"
  [ "$status" -eq 1 ]
}

@test "queue expanded row offers Open PR and Open terminal from model data" {
  local view="$MODS/queue/view.js"
  local main="$MODS/queue/main.js"
  grep -q 'ghostButton(`queue-open-pr-${id}`, "Open PR"' "$view"
  grep -q 'ghostButton(`queue-open-term-${id}`, "Open terminal"' "$view"
  grep -q 'row.subject' "$view"
  grep -q 'row.terminal' "$view"
  grep -q 'write_to_clipboard' "$main"
  grep -q 'focusSteps(' "$main"
  run grep -nE 'parsePrKey|subjectOf|focusSteps' "$view"
  [ "$status" -eq 1 ]
}

@test "gauge is a static text glyph in ui.js, not a pill" {
  grep -q 'export function gauge(content, color)' "$MODS/_ui/ui.js"
  grep -q 'text("code", content, color ?? theme().muted)' "$MODS/_ui/ui.js"
  run grep -nE 'export function gauge\([^)]*\)[^{]*\{[^}]*pill' "$MODS/_ui/ui.js"
  [ "$status" -eq 1 ]
}

@test "queue row detail draws detailModel through keyValue" {
  grep -q 'export function keyValue(' "$MODS/_ui/ui.js"
  grep -q 'text("section", row.key, t.muted)' "$MODS/_ui/ui.js"
  grep -q 'text(row.mono ? "code" : "meta", row.value)' "$MODS/_ui/ui.js"
  local view="$MODS/queue/view.js"
  grep -q 'const model = detailModel(row, opts.nowMs)' "$view"
  grep -q 'keyValue(model.facts)' "$view"
  grep -Fq 'selectableCode(`queue-cmd-${id}`, [model.command])' "$view"
  grep -q 'caption(model.timing)' "$view"
  grep -Fq 'model.logNote != null ? caption(model.logNote)' "$view"
  run grep -nE 'commandText\(|caption\(`cwd' "$view"
  [ "$status" -eq 1 ]
}

@test "detail header: disabled Open for Review carries the reason as a tooltip, not a caption line" {
  local detail="$MODS/prs/detail.js"
  grep -q 'button("detail-review", "Open for Review", () => onReview(), { disabled: !reviewState.enabled })' "$detail"
  grep -q '.tooltip(reviewState.reason)' "$detail"
  run grep -nF 'caption(reviewState.reason)' "$detail"
  [ "$status" -eq 1 ]
}

@test "run output: Show/Hide output is a status-row ghost, not a full-width toggle" {
  local detail="$MODS/prs/detail.js"
  grep -q 'ghostButton("out-tail", tailOpen ? "Hide output" : "Show output", onToggleTail)' "$detail"
  grep -q 'ghostButton("out-tab", "Open as tab", onTab)' "$detail"
  grep -q 'ghostButton("out-review", "View review"' "$detail"
  grep -q 'selectableCode("output"' "$detail"
  grep -q 'text("body", model.line, lineColor)' "$detail"
  run grep -nF '.child(ghostButton("out-tail"' "$detail"
  [ "$status" -eq 1 ]
  run grep -nF 'function landedRow' "$detail"
  [ "$status" -eq 1 ]
}

@test "PR search input has an explicit width: an Input in a flex_none row has none of its own" {
  grep -q 'textInput(this.prSearch, { width: LAYOUT.searchW })' "$MODS/prs/filter_bar.js"
  grep -q 'width == null ? el : el.w(width)' "$MODS/_ui/ui.js"
  grep -q 'searchW:' "$MODS/_lib/tokens.js"
}

@test "every text field goes through textInput, which sets the body type role" {
  grep -q 'export function textInput(' "$MODS/_ui/ui.js"
  grep -q 'type(Input.new(state), "body")' "$MODS/_ui/ui.js"
  run grep -rn 'Input\.new(' "$MODS" --include=*.js --exclude=ui.js
  [ "$status" -eq 1 ]
}
