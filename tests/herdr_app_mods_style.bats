#!/usr/bin/env bats
# Style guards for the herdr-app mods: the brand DESIGN.md rules a grep can see.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_ui/*.js ai/herdr/app/mods/prs/*.js ai/herdr/app/mods/queue/*.js ai/herdr/app/mods/hosts/*.js ai/herdr/app/mods/sessions/*.js ai/herdr/app/mods/workbench/*.js ai/herdr/app/mods/workbench-button/*.js ai/herdr/app/mods/settings/*.js ai/herdr/app/mods/_lib/*.js

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
    "$MODS"/_ui/*.js "$MODS"/prs/*.js "$MODS"/queue/*.js "$MODS"/hosts/*.js "$MODS"/sessions/*.js "$MODS"/workbench/*.js "$MODS"/workbench-button/*.js "$MODS"/settings/*.js "$MODS"/_lib/panel_button.js
  [ "$status" -eq 1 ]
}

@test "filter rows go through checkRow; Checkbox.new lives only in _ui" {
  grep -q 'export function checkRow(' "$MODS/_ui/ui.js"
  run grep -n 'Checkbox.new(' "$MODS"/prs/*.js
  [ "$status" -eq 1 ]
}

@test "the brand forbids pills: no pill( call or export remains" {
  run grep -nE '\bpill\(' "$MODS"/_ui/*.js "$MODS"/prs/*.js "$MODS"/queue/*.js "$MODS"/hosts/*.js "$MODS"/sessions/*.js "$MODS"/workbench/*.js "$MODS"/workbench-button/*.js "$MODS"/settings/*.js
  [ "$status" -eq 1 ]
}

@test "the fail status glyph is ×, never ✗, which no brand font draws" {
  run grep -n '✗' "$MODS"/_ui/*.js "$MODS"/prs/*.js "$MODS"/queue/*.js "$MODS"/hosts/*.js "$MODS"/sessions/*.js "$MODS"/workbench/*.js "$MODS"/workbench-button/*.js "$MODS"/settings/*.js "$MODS"/_lib/*.js
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
  run grep -n 'glyph:' "$MODS"/_ui/*.js "$MODS"/prs/*.js "$MODS"/queue/*.js "$MODS"/hosts/*.js "$MODS"/sessions/*.js "$MODS"/workbench/*.js "$MODS"/workbench-button/*.js "$MODS"/settings/*.js
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

@test "back is one backButton, first in the header beside the title" {
  grep -qF 'export function backButton(' "$MODS/_ui/ui.js"
  grep -qF 'backButton("batch-back", onBack)' "$MODS/prs/batch_page.js"
  grep -qF 'backButton("detail-back"' "$MODS/prs/detail.js"
  grep -qF 'backButton("diff-back", onBack, "Files")' "$MODS/prs/files_view.js"
  run grep -nF 'button("batch-back"' "$MODS/prs/batch_page.js"
  [ "$status" -eq 1 ]
  run grep -nF 'button("detail-back"' "$MODS/prs/detail.js"
  [ "$status" -eq 1 ]
  run grep -nF 'button("diff-back"' "$MODS/prs/files_view.js"
  [ "$status" -eq 1 ]
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

@test "queue view draws lanes, staleness and spinning from the model" {
  local view="$MODS/queue/view.js"
  grep -q 'laneTitle(lane.label, lane.count)' "$view"
  grep -q 'spinning: props.spinning' "$view"
  grep -q 'warn: kind != null && kind !== "fresh"' "$view"
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

@test "queue main composes pageOf, machinePage, staleness and refreshSpinning" {
  local main="$MODS/queue/main.js"
  grep -q 'pageOf(navState(), machines)' "$main"
  grep -q 'machinePage(current, nowMs' "$main"
  grep -q 'staleness({ result, at: entry.at }, nowMs)' "$main"
  grep -q 'refreshSpinning(this.refreshSince, nowMs)' "$main"
  grep -q 'onToggleGroup: (on, row) => this.toggleGroup(on, row)' "$main"
}

@test "queue home draws homeModel cards and has no rows" {
  local view="$MODS/queue/view.js"
  local main="$MODS/queue/main.js"
  grep -q 'homeModel(machines, nowMs)' "$main"
  grep -q 'cards: model.cards' "$main"
  grep -Fq 'machineCard(`queue-machine-${block.key || "local"}`' "$view"
  grep -q 'title: block.title' "$view"
  grep -q 'counts: hide ? null : block.counts' "$view"
  grep -q 'hostNote: block.hostNote' "$view"
  grep -q 'statusText("warn", m.hostNote)' "$MODS/_ui/ui.js"
  grep -Fq 'children((opts.cards ?? []).map((block) => renderHomeCard(block, opts)))' "$view"
  run grep -nE 'homeModel|toggleCollapsed|slotGauge' "$view"
  [ "$status" -eq 1 ]
  run grep -nE 'props.blocks|block.section|block.open' "$view"
  [ "$status" -eq 1 ]
}

@test "queue home card opens selectMachine" {
  local view="$MODS/queue/view.js"
  local main="$MODS/queue/main.js"
  grep -q '() => opts.onSelectMachine(block.key)' "$view"
  grep -q 'selectMachine(navState(), key)' "$main"
  grep -q 'setShared("queue.machine"' "$main"
  grep -q 'shared("queue.machine")' "$main"
  run grep -nE 'toggleCollapsed|queue.collapsed|onToggleMachine' "$view" "$main"
  [ "$status" -eq 1 ]
}

@test "queue machine page uses pageOf and machinePage" {
  local view="$MODS/queue/view.js"
  grep -q 'opts.page == null ? renderHome(opts) : renderMachine(opts)' "$view"
  grep -q 'page.summary' "$view"
  grep -q 'page.lanes' "$view"
  grep -q 'hostNote: summary.hostNote' "$view"
  run grep -nF 'heavy waits:' "$view"
  [ "$status" -eq 1 ]
}

@test "queue machine page has a Machines back button" {
  local view="$MODS/queue/view.js"
  grep -q 'button("queue-back", "‹ Machines"' "$view"
  grep -q 'opts.onBack()' "$view"
}

@test "queue sections are cards; empty is a header line with no None" {
  local view="$MODS/queue/view.js"
  grep -Fq 'card(`queue-lane-${on ?? "local"}-${lane.key}`, { linked: false })' "$view"
  grep -q 'laneTitle(lane.label, lane.count)' "$view"
  grep -q 'if (!lane.card)' "$view"
  grep -q 'sectionLabel(lane.label, lane.count)' "$view"
  run grep -nF 'placeholder("None")' "$view"
  [ "$status" -eq 1 ]
}

@test "queue has no filter or segmented control" {
  local view="$MODS/queue/view.js"
  local main="$MODS/queue/main.js"
  run grep -nE 'segmented\(|onFilter|this.filter|filterRows|caption\(props.summary\)' "$view" "$main"
  [ "$status" -eq 1 ]
}

@test "queue groups expand through the model keys" {
  local view="$MODS/queue/view.js"
  local main="$MODS/queue/main.js"
  grep -q 'groupKey = (on, row) =>' "$main"
  grep -q 'row.key' "$main"
  grep -q 'shared("queue.groups")' "$main"
  grep -Fq 'opts.openGroups?.[opts.groupKey(on, row)]' "$view"
  grep -q 'onToggleGroup(on, row)' "$view"
  run grep -nF 'onToggleGroup(on, row.id)' "$view"
  [ "$status" -eq 1 ]
}

@test "queue expanded row offers Open PR and Open terminal from model data" {
  local view="$MODS/queue/view.js"
  local main="$MODS/queue/main.js"
  grep -q 'button(`queue-open-pr-${id}`, "Open PR"' "$view"
  grep -q 'button(`queue-open-term-${id}`, "Open terminal"' "$view"
  grep -q 'row.subject' "$view"
  grep -q 'row.terminal' "$view"
  grep -q 'write_to_clipboard' "$main"
  grep -q 'focusSteps(' "$main"
  run grep -nE 'parsePrKey|subjectOf|focusSteps' "$view"
  [ "$status" -eq 1 ]
}

@test "destructive confirm uses dangerButton and Keep, and Batch Cancel asks first" {
  local ui="$MODS/_ui/ui.js"
  local queue="$MODS/queue/view.js"
  local page="$MODS/prs/batch_page.js"
  local launcher="$MODS/prs/batch_launcher.js"
  grep -qF 'export function dangerButton(' "$ui"
  grep -qF 'dangerButton(`queue-confirm-${id}`, "Confirm cancel"' "$queue"
  grep -qF 'caption("Stops this job")' "$queue"
  grep -qF 'dangerButton(`confirm-${killKey}`' "$page"
  grep -qF 'button(`keep-${killKey}`, "Keep", () => L.dismissCancel())' "$page"
  grep -qF 'L.askCancel(on, id, false)' "$page"
  grep -qF 'L.askCancel(on, id, true)' "$page"
  grep -qF 'askCancel(on, id, kill) {' "$launcher"
  grep -qF 'dismissCancel() {' "$launcher"
  run grep -nF 'button(`queue-confirm-' "$queue"
  [ "$status" -eq 1 ]
  run grep -nF 'confirm ? "Confirm kill" : "Kill"' "$page"
  [ "$status" -eq 1 ]
  run grep -nF 'L.cancel(on, id, false)' "$page"
  [ "$status" -eq 1 ]
  run grep -rn confirmKill "$MODS/prs"
  [ "$status" -eq 1 ]
}

@test "meter draws via meter() and the queue has no block glyphs" {
  local ui="$MODS/_ui/ui.js"
  local view="$MODS/queue/view.js"
  grep -q 'export function meter(model' "$ui"
  grep -q 'meterTrack(model, fill, hair)' "$ui"
  grep -q '.w(METER.w)' "$ui"
  grep -q 'meter(mt.model, { muted })' "$ui"
  run grep -nE 'export function gauge\(|gauge\(' "$ui" "$view"
  [ "$status" -eq 1 ]
  run grep -nE '█|░' "$ui" "$view" "$MODS/queue/main.js"
  [ "$status" -eq 1 ]
}

@test "Queue draws machines through the shared machine pieces" {
  local view="$MODS/queue/view.js"
  grep -q 'machineCard(' "$view"
  grep -q 'machineHeader(' "$view"
  grep -q 'paneHeader(' "$view"
  run grep -nF 'function renderMeters' "$view"
  [ "$status" -eq 1 ]
  run grep -nF 'function staleLine' "$view"
  [ "$status" -eq 1 ]
  run grep -nF 'meter(' "$view"
  [ "$status" -eq 1 ]
}

@test "Hosts draws machines through the shared machine pieces" {
  local view="$MODS/hosts/view.js"
  grep -q 'machineCard(' "$view"
  grep -q 'machineHeader(' "$view"
  grep -q 'paneHeader(' "$view"
  run grep -nF 'function renderMeter' "$view"
  [ "$status" -eq 1 ]
  run grep -nF 'function staleLine' "$view"
  [ "$status" -eq 1 ]
  run grep -nF 'meter(' "$view"
  [ "$status" -eq 1 ]
  run grep -nF 'gauge(' "$view"
  [ "$status" -eq 1 ]
}

@test "iconButton draws the brand outline edge" {
  local ui="$MODS/_ui/ui.js"
  awk '/^export function iconButton/,/^}/' "$ui" | grep -qF 'border_color(t.input)'
}

@test "full screen is an iconButton toggle with a selected state" {
  local ui="$MODS/_ui/ui.js"
  local view="$MODS/prs/board_view.js"
  awk '/^export function iconButton/,/^}/' "$ui" | grep -qF 'selected = false'
  grep -qF 'iconButton("fullscreen", "mods/prs/fullscreen.svg"' "$view"
  grep -qF 'selected: state === "on"' "$view"
  grep -qF 'state === "off" ? "Full screen" : "Exit full screen"' "$view"
  [[ -f "$MODS/prs/fullscreen.svg" ]]
  run grep -nF 'button("fullscreen"' "$view"
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
  grep -qF 'button("detail-review", "Open for Review", () => onReview(), { disabled: !reviewState.enabled, reason: reviewState.reason })' "$detail"
  run grep -nF '.tooltip(reviewState.reason)' "$detail"
  [ "$status" -eq 1 ]
  run grep -nF 'caption(reviewState.reason)' "$detail"
  [ "$status" -eq 1 ]
}

@test "Batch start and Clear sit in an actionBar with the reason beside them" {
  local ui="$MODS/_ui/ui.js"
  local page="$MODS/prs/batch_page.js"
  local detail="$MODS/prs/detail.js"
  grep -qF '.child(renderStartBar(' "$page"
  grep -qF 'solidButton("batch-start", "Start", () => L.launch(prs), { disabled, reason })' "$page"
  grep -qF 'button("batch-clear", "Clear", () => L.reset(), { disabled: !L.canClear()' "$page"
  grep -qF 'export function actionBar(' "$ui"
  grep -qF 'border_t_1()' "$ui"
  grep -qF 'e.tooltip(reason)' "$ui"
  grep -qF 'reason: reviewState.reason' "$detail"
  run grep -nF '.when(Boolean(reason), (e) => e.child(text("body", reason' "$page"
  [ "$status" -eq 1 ]
  run grep -nF '.tooltip(reviewState.reason)' "$detail"
  [ "$status" -eq 1 ]
  run grep -nF 'selected PR(s) hidden by filters won'\''t run' "$page"
  [ "$status" -eq 1 ]
}

@test "run output: Show/Hide output is a status-row outline button, not a full-width toggle" {
  local detail="$MODS/prs/detail.js"
  grep -q 'button("out-tail", tailOpen ? "Hide output" : "Show output", onToggleTail)' "$detail"
  grep -q 'button("out-tab", "Open as tab", onTab)' "$detail"
  grep -q 'button("out-review", "View review"' "$detail"
  grep -q 'selectableCode("output"' "$detail"
  grep -q 'text("body", model.line, lineColor)' "$detail"
  run grep -nF '.child(button("out-tail"' "$detail"
  [ "$status" -eq 1 ]
  run grep -nF 'function landedRow' "$detail"
  [ "$status" -eq 1 ]
}

@test "posting confirm is a solid button next to outline Cancel" {
  local detail="$MODS/prs/detail.js"
  grep -qF 'solidButton("act-confirm", `Confirm: ${confirm.label}`, onConfirm)' "$detail"
  run grep -nF 'button("act-confirm"' "$detail"
  [ "$status" -eq 1 ]
}

@test "mods never use ghostButton: a labelled action is an outline button" {
  run grep -rn 'ghostButton' "$MODS"
  [ "$status" -eq 1 ]
}

@test "PR search input has an explicit width: an Input in a flex_none row has none of its own" {
  grep -q 'textInput(this.prSearch, { width: LAYOUT.searchW })' "$MODS/prs/filter_bar.js"
  grep -q 'width == null ? el : el.w(width)' "$MODS/_ui/ui.js"
  grep -q 'searchW:' "$MODS/_lib/tokens.js"
}

@test "one menuButton draws facet and Models triggers; Models always shows its value" {
  grep -qF 'export function menuButton(' "$MODS/_ui/ui.js"
  grep -qF 'menuButton(f.label, { active: f.active })' "$MODS/prs/filter_bar.js"
  grep -qF 'menuButton("Models"' "$MODS/prs/model_picker.js"
  grep -qF '"Configured defaults"' "$MODS/prs/model_picker.js"
  run grep -nF '.rounded(RADIUS.control).border_1().border_color(t.border)' "$MODS/prs/filter_bar.js"
  [ "$status" -eq 1 ]
  run grep -nF '.rounded(RADIUS.control).border_1().border_color(t.border)' "$MODS/prs/model_picker.js"
  [ "$status" -eq 1 ]
  run grep -nF '.when(Boolean(summary), (e) => e.child(caption(summary)))' "$MODS/prs/model_picker.js"
  [ "$status" -eq 1 ]
}

@test "every text field goes through textInput, which sets the body type role" {
  grep -q 'export function textInput(' "$MODS/_ui/ui.js"
  grep -q 'type(Input.new(state), "body")' "$MODS/_ui/ui.js"
  run grep -rn 'Input\.new(' "$MODS" --include=*.js --exclude=ui.js
  [ "$status" -eq 1 ]
}

@test "no literal colour in mod code: colours come only from theme()" {
  # Quoted hex (#rgb, #rgba, #rrggbb, #rrggbbaa), rgb()/rgba()/hsl()/hsla() calls, 0xRRGGBB.
  run grep -rnE --include='*.js' \
    "[\"'\`]#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})[\"'\`]|\\b(rgba?|hsla?)\\(|\\b0x[0-9a-fA-F]{6}\\b" \
    "$MODS"
  [ "$status" -eq 1 ]
}

# A mod imports `_lib` and `_ui` through its own installed copy, which node cannot load here (the
# files import herdr), so a name removed from a shared module only fails when the app opens the mod.
@test "every name a mod imports from _lib or _ui is exported by that module" {
  run node --input-type=module -e '
    import fs from "node:fs";
    import path from "node:path";
    const mods = process.argv[1];
    const files = [];
    const walk = (d) => {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, e.name);
        if (e.isDirectory()) walk(p);
        else if (p.endsWith(".js") && !p.endsWith(".generated.js")) files.push(p);
      }
    };
    walk(mods);
    const missing = [];
    for (const file of files) {
      const src = fs.readFileSync(file, "utf8");
      for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from\s*["\x27](\.[^"\x27]+)["\x27]/g)) {
        let target = path.resolve(path.dirname(file), m[2]);
        if (!fs.existsSync(target)) {
          const rel = path.relative(mods, target).replace(/^[^/]+\//, "");
          target = path.join(mods, rel);
        }
        if (!fs.existsSync(target)) { missing.push(`${path.relative(mods, file)}: ${m[2]} does not resolve`); continue; }
        const dest = fs.readFileSync(target, "utf8");
        for (const raw of m[1].split(",")) {
          const name = raw.trim().split(/\s+as\s+/)[0];
          if (!name) continue;
          const decl = new RegExp(`export\\s+(?:async\\s+)?(?:function\\*?|const|let|class)\\s+${name}\\b`);
          const list = new RegExp(`export\\s*\\{[^}]*\\b${name}\\b`);
          if (!decl.test(dest) && !list.test(dest)) missing.push(`${path.relative(mods, file)}: ${name} is not exported by ${m[2]}`);
        }
      }
    }
    process.stdout.write(missing.join("\n"));
    process.exit(missing.length ? 1 : 0);
  ' "$MODS"
  [ "$status" -eq 0 ] || { echo "$output" >&3; return 1; }
}

@test "batching starts from Select for batch: no lane draws its own Batch button, board and compact share laneSelect" {
  run grep -rnE 'batchAction|onBatch|batchFor' "$MODS"/prs/*.js
  [ "$status" -eq 1 ]
  grep -q 'export function laneSelect' "$MODS/prs/board.js"
  grep -q 'laneSelect(s, select)' "$MODS/prs/compact.js"
  grep -q '"Select for batch"' "$MODS/prs/board_view.js"
}

@test "render leaves selecting without notifying: endSelecting is the silent one" {
  grep -q 'this.endSelecting();' "$MODS/prs/board_view.js"
  run grep -n 'if (tabOf() !== "mine" && this.selecting) this.stopSelecting' "$MODS/prs/board_view.js"
  [ "$status" -eq 1 ]
  grep -q '  clearSelection() {' "$MODS/prs/batch_launcher.js"
}

@test "card clips: min_w(0) and overflow_hidden on the card itself" {
  grep -A6 'export function card(' "$MODS/_ui/ui.js" | grep -q 'min_w(0)'
  grep -A6 'export function card(' "$MODS/_ui/ui.js" | grep -q 'overflow_hidden()'
}

@test "settings pane uses menuButton, keyValue, actionBar and Sync solidButton with no stub placeholder" {
  grep -q 'menuButton(' "$MODS/settings/view.js"
  grep -q 'keyValue(' "$MODS/settings/view.js"
  grep -q 'actionBar(' "$MODS/settings/view.js"
  grep -q 'solidButton("settings-sync", "Sync"' "$MODS/settings/view.js"
  run grep -n 'placeholder("Settings")' "$MODS/settings/view.js"
  [ "$status" -eq 1 ]
}

@test "sessions home uses machineCard; machine page uses Machines back" {
  grep -q 'machineCard(' "$MODS/sessions/view.js"
  grep -q '‹ Machines' "$MODS/sessions/view.js"
  grep -q 'sessions.open' "$MODS/sessions/main.js"
}
