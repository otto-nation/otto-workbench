#!/usr/bin/env bats
# Parity is structural: the mod's and the web's views never compose board pulls, track owed, or
# derive lanes, facets, lane status or lane presets themselves; _lib/board_store.js and
# _lib/board_model.js do, for both. A view asks wantFor(tab) what its tab needs and draws the rest.
#
# Owner: ai/herdr/app/mods/AGENTS.md section Board code lives in _lib. Views only choose targets,
# say what they show, schedule and draw — use the shared store/model in _lib.
#
# Accepted limit: a source scan cannot see computed identifiers (`c['group'+'Columns']`) or
# constructed strings. The guard catches imports, aliases, namespace use, re-exports and
# dynamic import() of banned symbols and files.
#
# Discovery is recursive over ai/herdr/web/** and ai/herdr/app/mods/prs/** (`*.js`).
# Roots default to those trees; PARITY_ROOTS (newline-separated) or extra argv override them
# so a throwaway tree under BATS_TEST_TMPDIR can be scanned the same way.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/prs/*.js ai/herdr/app/mods/_lib/*.js ai/herdr/web/* tests/herdr_board_parity_scan.mjs

setup() {
  load 'test_helper'
  common_setup
  MODS="$REPO_ROOT/ai/herdr/app/mods"
  WEB="$REPO_ROOT/ai/herdr/web"
  SCANNER="$REPO_ROOT/tests/herdr_board_parity_scan.mjs"
  mapfile -t VIEWS < <(views)
}
teardown() { common_teardown; }

OWNER='ai/herdr/app/mods/AGENTS.md section Board code lives in _lib — use the shared store/model in _lib'

scan() {
  local re="$1"
  shift
  WEB="$WEB" MODS="$MODS" REPO_ROOT="$REPO_ROOT" run node "$SCANNER" "$re" "$@"
}

assert_clean() {
  [ "$status" -eq 0 ]
  if [[ -n "$output" ]]; then
    echo "$OWNER:" >&2
    echo "$output" >&2
    return 1
  fi
}

views() {
  find "$WEB" "$MODS/prs" -name '*.js' | LC_ALL=C sort
}

PULLS='pullTarget|pullOwed|loadBoard|docToState|mergeRows|withRunning|loadTerminals|runningFor|representatives|staleKeys|boardState|mergeStates|owedDue|OWED_TTL_MS|owedState|owedError|owedAt|assemble|loadOpenPrs|loadListing|loadLanded|loadRepo|loadUser|loadOwed|loadBatchDoc|loadBusy|board_pull|board_doc|board_data'
# LANE_NEEDS/needStatus: a view never decides which lane waits on what, or its status; it draws
# lane.status/lane.message. wantFor is allowed: a host asks it what its tab needs.
LANES='groupColumns|visibleColumns|facetView|filterPrs|columnsFor|compactSections|teamColumn|mineColumn|TEAM_COLUMNS|MINE_COLUMNS|columnPreset|filterFacets|filterLabels|filterRepos|LANE_NEEDS|needStatus'
# The Batch pages build the Batch form's own model (_lib/batch_launch.js); their orchestration is
# otto-nation/otto-workbench#1943's remaining half, not this board's.
LANE_EXEMPT='/prs/batch_page.js:|/web/public/batch_page.js:'

@test "parity: no view composes a board pull; only _lib/board_store.js does" {
  scan "$PULLS" "${VIEWS[@]}"
  assert_clean
}

@test "parity: no view derives lanes, facets or lane presets; only _lib/board_model.js does" {
  scan "$LANES" "${VIEWS[@]}"
  [ "$status" -eq 0 ]
  run bash -c "printf '%s\n' \"\$1\" | grep -vE '$LANE_EXEMPT' | grep ." _ "$output"
  if [[ "$status" -ne 1 ]]; then
    echo "$OWNER:" >&2
    echo "$output" >&2
    return 1
  fi
}

@test "parity: within _lib only board_store.js reaches pullTarget, and only board_pull.js reaches loadBoard" {
  local libs
  mapfile -t libs < <(find "$MODS"/_lib -name '*.js' | grep -vE '/(board_store|board_pull)\.js$' | LC_ALL=C sort)
  scan 'pullTarget|board_pull' "${libs[@]}"
  assert_clean
  mapfile -t libs < <(find "$MODS"/_lib -name '*.js' | grep -vE '/(board_pull|board_data)\.js$' | LC_ALL=C sort)
  scan 'loadBoard' "${libs[@]}"
  assert_clean
}

@test "parity: both views use the shared store and model, and ask the model what their tab wants" {
  grep -q 'createBoardStore' "$MODS/prs/board_view.js"
  grep -q 'createBoardStore' "$WEB/server/board.js"
  grep -q 'boardModel' "$MODS/prs/board_view.js"
  grep -q "from '../../app/mods/_lib/board_model.js'" "$WEB/lib/view.js"
  grep -q 'wantFor(tabOf())' "$MODS/prs/board_view.js"
  grep -q 'wantFor(v).owed' "$WEB/server/main.js"
}

@test "parity: no view decides owed from the tab name itself" {
  # `.mine.` is 'mine' or "mine" in either quote; a line comparing a tab to it and naming owed fails.
  run grep -nE 'owed.*==+ *.mine.|.mine. *==+.*owed' "${VIEWS[@]}"
  [ "$status" -eq 1 ]
}

@test "parity: a nested file with a banned import is reported" {
  local root="$BATS_TEST_TMPDIR/parity"
  mkdir -p "$root/web/nested"
  printf '%s\n' "import { groupColumns } from './x.js';" > "$root/web/nested/deep.js"
  scan "$LANES" "$root"
  [ "$status" -eq 0 ]
  [[ "$output" == *nested/deep.js:groupColumns* ]]
}

@test "parity: a banned identifier only inside a comment or a string is not reported" {
  local root="$BATS_TEST_TMPDIR/parity"
  mkdir -p "$root/web"
  printf '%s\n' \
    "// groupColumns" \
    "/* groupColumns */" \
    "const a = 'groupColumns';" \
    'const b = "groupColumns";' \
    "const c = \`groupColumns\`;" \
    'const u = "http://groupColumns";' \
    > "$root/web/words.js"
  scan "$LANES" "$root"
  assert_clean
}

@test "parity: aliased, namespace and re-export board_data uses are reported" {
  local root="$BATS_TEST_TMPDIR/parity"
  mkdir -p "$root/web"
  printf '%s\n' "import { loadBoard as lb } from './x.js';" > "$root/web/alias.js"
  printf '%s\n' "import * as bd from './x.js'; bd.loadBoard();" > "$root/web/ns.js"
  printf '%s\n' "export * from './board_data.js';" > "$root/web/reexport.js"
  scan "$PULLS" "$root"
  [ "$status" -eq 0 ]
  [[ "$output" == *alias.js:loadBoard* ]]
  [[ "$output" == *ns.js:loadBoard* ]]
  [[ "$output" == *reexport.js:board_data* ]]
}

@test "parity: a crashed scanner fails the test" {
  scan "$PULLS" "$BATS_TEST_TMPDIR/no-such-parity-root"
  [ "$status" -ne 0 ]
}

@test "parity: both views open a review run through toggleOpen, never a truthiness test on the open key" {
  grep -q 'toggleOpen' "$MODS/prs/main.js"
  grep -q 'toggleOpen' "$WEB/public/app.js"
  run grep -nE 'if \(this\.openStamp\)|!S\.detail\.openStamp|if \(this\.openKey\)|!S\.detail\.openKey' \
    "$MODS/prs/main.js" "$WEB/public/app.js"
  [ "$status" -eq 1 ]
}

@test "parity: the two batch_page.js exemptions still hold" {
  [[ "$LANE_EXEMPT" == *"/prs/batch_page.js:"* ]]
  [[ "$LANE_EXEMPT" == *"/web/public/batch_page.js:"* ]]
  scan "$LANES" "${VIEWS[@]}"
  [ "$status" -eq 0 ]
  [[ "$output" == *"/prs/batch_page.js:"* ]]
  run bash -c "printf '%s\n' \"\$1\" | grep -vE '$LANE_EXEMPT' | grep ." _ "$output"
  if [[ "$status" -ne 1 ]]; then
    echo "$OWNER:" >&2
    echo "$output" >&2
    return 1
  fi
}
