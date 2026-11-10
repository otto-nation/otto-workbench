#!/usr/bin/env bats
# Tests for _herdr_app_install_layouts — derive herdr-app layouts from main.json.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/layouts_install.sh ai/herdr/steps.sh ai/herdr/app/layouts/*.jq lib/constants.sh

setup() {
  load 'test_helper'
  common_setup
  . "$REPO_ROOT/lib/portable.sh"
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  APP="$HOME/.config/herdr-app"
  SRC="$BATS_TEST_TMPDIR/layouts-src"
  mkdir -p "$SRC"
  cp "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" "$SRC/"
  MANIFEST="$BATS_TEST_TMPDIR/herdr-app-layouts.manifest"
  FIXTURE="$REPO_ROOT/tests/fixtures/herdr-app-main.json"
  unset HERDR_ENV HERDR_PANE_ID
  narrow_path_to bash jq
  UNTRUSTED="$BATS_TEST_TMPDIR/untrusted-app"
}

teardown() {
  common_teardown
}

_run() {
  bash -c '
    set -e
    export NO_COLOR=1
    WORKBENCH_DIR="$1"
    export WORKBENCH_STABLE_DIR="$1"
    . "$1/lib/ui.sh"
    . "$1/ai/herdr/steps.sh"
    HERDR_APP_LAYOUTS_SRC_DIR="$3"
    _herdr_app_install_layouts "$2" "$4"
  ' _ "$REPO_ROOT" "$APP" "$SRC" "$MANIFEST"
}

_seed_main() {
  mkdir -p "$APP/layouts"
  cp "$FIXTURE" "$APP/layouts/main.json"
}

_cksum() {
  cksum "$1" | awk '{print $1}'
}

_run_step() {
  bash -c '
    set -e
    export NO_COLOR=1
    WORKBENCH_DIR="$1"
    export WORKBENCH_STABLE_DIR="$1"
    . "$1/lib/ui.sh"
    . "$1/ai/herdr/steps.sh"
    HERDR_APP_LAYOUTS_SRC_DIR="$2"
    HERDR_UNTRUSTED_APP_DIR="$3"
    HERDR_APP_LAYOUTS_MANIFEST="$4"
    HERDR_UNTRUSTED_APP_LAYOUTS_MANIFEST="$4.untrusted"
    step_herdr_app_layouts
  ' _ "$REPO_ROOT" "$SRC" "$UNTRUSTED" "$MANIFEST"
}

@test "jq leaves sidebar-lists as herdr ships it" {
  run jq -f "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" "$FIXTURE"
  [ "$status" -eq 0 ]
  expected=$(jq '[.. | objects | select(.id? == "sidebar-lists") | .children | map(.id)]' "$FIXTURE")
  actual=$(jq '[.. | objects | select(.id? == "sidebar-lists") | .children | map(.id)]' <<< "$output")
  [ "$expected" = "$actual" ]
  jq -e '[.. | objects | select(.id? == "prs")] | length == 0' <<< "$output"
}

@test "jq adds the board to panel-body after files and changes" {
  run jq -f "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" "$FIXTURE"
  [ "$status" -eq 0 ]
  jq -e '[.. | objects | select(.id? == "panel-body") | .children | map(.id)] == [["files","changes","prs-panel"]]' <<< "$output"
  jq -e '[.. | objects | select(.id? == "prs-panel") | .content.mod] == ["prs"]' <<< "$output"
}

@test "jq wraps the tab bar in a row with the PRs toggle" {
  run jq -f "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" "$FIXTURE"
  [ "$status" -eq 0 ]
  jq -e '[.. | objects | select(.id? == "work") | .children | map(.id) | .[0]] == ["tabs-row"]' <<< "$output"
  jq -e '[.. | objects | select(.id? == "tabs-row") | .children | map(.id)] == [["tabs","prs-button"]]' <<< "$output"
  jq -e '[.. | objects | select(.id? == "tabs-row") | .size] == [{"px":42}]' <<< "$output"
  jq -e '[.. | objects | select(.id? == "prs-button") | .content.mod] == ["prs-button"]' <<< "$output"
  jq -e '[.. | objects | select(.id? == "tabs") | .size] == [{"weight":1}]' <<< "$output"
}

@test "jq errors when panel-body is missing" {
  input=$(jq 'walk(if type == "object" and has("children") then .children |= map(select(.id? != "panel-body")) else . end)' "$FIXTURE")
  run jq -f "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" <<< "$input"
  [ "$status" -ne 0 ]
}

@test "jq is idempotent on its own output" {
  once=$(jq -f "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" "$FIXTURE")
  twice=$(jq -f "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" <<< "$once")
  [ "$once" = "$twice" ]
}

@test "fresh install writes the layout and a manifest line" {
  _seed_main
  run _run
  [ "$status" -eq 0 ]
  [ -f "$APP/layouts/workbench.json" ]
  grep -qx $'workbench\t'"$(_cksum "$APP/layouts/workbench.json")" "$MANIFEST"
  [[ "$output" == *"herdr-app layout workbench"* ]]
}

@test "re-run is a quiet no-op with WORKBENCH_SYNC=true" {
  _seed_main
  run _run
  [ "$status" -eq 0 ]
  touch -t 202001010000 "$APP/layouts/workbench.json"
  before_sum=$(_cksum "$APP/layouts/workbench.json")
  before_mtime=$(file_mtime "$APP/layouts/workbench.json")
  WORKBENCH_SYNC=true run _run
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ "$(_cksum "$APP/layouts/workbench.json")" = "$before_sum" ]
  [ "$(file_mtime "$APP/layouts/workbench.json")" = "$before_mtime" ]
}

@test "main.json changed upstream regenerates the layout" {
  _seed_main
  run _run
  [ "$status" -eq 0 ]
  before=$(_cksum "$APP/layouts/workbench.json")
  jq '.children[0].size.px = 300' "$FIXTURE" > "$APP/layouts/main.json"
  run _run
  [ "$status" -eq 0 ]
  [ "$(_cksum "$APP/layouts/workbench.json")" != "$before" ]
  jq -e '[.. | objects | select(.id? == "sidebar") | .size.px] == [300]' \
    "$APP/layouts/workbench.json"
}

@test "absent main.json writes nothing" {
  run _run
  [ "$status" -eq 0 ]
  [ ! -e "$APP/layouts/workbench.json" ]
  [[ "$output" == *"start herdr-app once"* ]]
}

@test "main.json without panel-body warns and writes nothing" {
  mkdir -p "$APP/layouts"
  printf '%s\n' '{"id":"root"}' > "$APP/layouts/main.json"
  run _run
  [ "$status" -eq 0 ]
  [ ! -e "$APP/layouts/workbench.json" ]
  [[ "$output" == *"could not derive"* ]]
}

@test "hand-edited layout is warned and kept" {
  _seed_main
  run _run
  [ "$status" -eq 0 ]
  recorded=$(awk -F'\t' '$1=="workbench"{print $2}' "$MANIFEST")
  printf '%s\n' '{"id":"hand"}' > "$APP/layouts/workbench.json"
  run _run
  [ "$status" -eq 0 ]
  [ "$(cat "$APP/layouts/workbench.json")" = '{"id":"hand"}' ]
  [[ "$output" == *"left as is"* ]]
  grep -qx $'workbench\t'"$recorded" "$MANIFEST"
}

# herdr's layout.set (or a person restoring the file) can leave it equal to what
# the workbench would write now while the manifest still records an older copy.
@test "a layout already matching the workbench copy is adopted, not called an edit" {
  _seed_main
  run _run
  [ "$status" -eq 0 ]
  jq '.children[0].size.px = 300' "$FIXTURE" > "$APP/layouts/main.json"
  jq -f "$SRC/workbench.jq" "$APP/layouts/main.json" > "$APP/layouts/workbench.json"
  run _run
  [ "$status" -eq 0 ]
  [[ "$output" != *"left as is"* ]]
  grep -qx $'workbench\t'"$(_cksum "$APP/layouts/workbench.json")" "$MANIFEST"
}

# herdr's layout.set saves the same tree with its own key order.
@test "a layout matching the workbench copy in another key order is adopted" {
  _seed_main
  run _run
  [ "$status" -eq 0 ]
  jq '.children[0].size.px = 300' "$FIXTURE" > "$APP/layouts/main.json"
  jq -f "$SRC/workbench.jq" "$APP/layouts/main.json" | jq -S . > "$APP/layouts/workbench.json"
  run _run
  [ "$status" -eq 0 ]
  [[ "$output" != *"left as is"* ]]
  grep -qx $'workbench\t'"$(_cksum "$APP/layouts/workbench.json")" "$MANIFEST"
}

@test "manifest-less identical layout is adopted" {
  _seed_main
  jq -f "$SRC/workbench.jq" "$APP/layouts/main.json" > "$APP/layouts/workbench.json"
  before=$(_cksum "$APP/layouts/workbench.json")
  run _run
  [ "$status" -eq 0 ]
  [ "$(_cksum "$APP/layouts/workbench.json")" = "$before" ]
  grep -qx $'workbench\t'"$before" "$MANIFEST"
  [[ "$output" != *"left as is"* ]]
}

@test "manifest-less different layout is left alone" {
  _seed_main
  printf '%s\n' '{"id":"mine"}' > "$APP/layouts/workbench.json"
  run _run
  [ "$status" -eq 0 ]
  [ "$(cat "$APP/layouts/workbench.json")" = '{"id":"mine"}' ]
  [[ "$output" == *"left as is"* ]]
  [ ! -f "$MANIFEST" ] || ! grep -q $'^workbench\t' "$MANIFEST"
}

@test "shipped layout name in installed.json is refused" {
  _seed_main
  printf '%s\n' '{"id":"shipped"}' > "$APP/layouts/workbench.json"
  printf '%s\n' '{"layout:workbench":{}}' > "$APP/installed.json"
  run _run
  [ "$status" -eq 0 ]
  [ "$(cat "$APP/layouts/workbench.json")" = '{"id":"shipped"}' ]
  [[ "$output" == *"shipped"* ]]
  [ ! -f "$MANIFEST" ] || ! grep -q $'^workbench\t' "$MANIFEST"
}

@test "a hand-edited layout is not regenerated after main.json changes" {
  _seed_main
  run _run
  [ "$status" -eq 0 ]
  recorded=$(awk -F'\t' '$1=="workbench"{print $2}' "$MANIFEST")
  printf '%s\n' '{"id":"hand"}' > "$APP/layouts/workbench.json"
  jq '.children[0].size.px = 300' "$FIXTURE" > "$APP/layouts/main.json"
  run _run
  [ "$status" -eq 0 ]
  [ "$(cat "$APP/layouts/workbench.json")" = '{"id":"hand"}' ]
  grep -qx $'workbench\t'"$recorded" "$MANIFEST"
  [[ "$output" == *"left as is"* ]]
}

@test "step is a no-op when herdr-app is absent" {
  run _run_step
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ ! -e "$APP" ]
}

@test "step derives the same layouts in an existing untrusted app dir" {
  _seed_main
  mkdir -p "$UNTRUSTED/layouts"
  cp "$FIXTURE" "$UNTRUSTED/layouts/main.json"
  printf '%s\n' keep > "$UNTRUSTED/layouts/KEEP"
  run _run_step
  [ "$status" -eq 0 ]
  [ "$(cat "$UNTRUSTED/layouts/KEEP")" = keep ]
  cmp "$APP/layouts/workbench.json" "$UNTRUSTED/layouts/workbench.json"
  grep -q $'^workbench\t' "$MANIFEST.untrusted"
  [ "$(grep -c . "$MANIFEST")" -eq "$(grep -c . "$MANIFEST.untrusted")" ]
  ! cmp -s "$MANIFEST" "$MANIFEST.untrusted" || [ "$MANIFEST" != "$MANIFEST.untrusted" ]
}

@test "step does not create the untrusted app dir" {
  _seed_main
  run _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$UNTRUSTED" ]
}

@test "step prints the layout.switch hint on first install only" {
  _seed_main
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *'herdr-app: show the workbench layout (a PRs toggle beside Files/Changes opens the board) with: herdr-app do layout.switch --name workbench'* ]]
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" != *'layout.switch --name workbench'* ]]
}
