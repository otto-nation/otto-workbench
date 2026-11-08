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

@test "jq puts prs third under sidebar-lists with sidebar view" {
  run jq -f "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" "$FIXTURE"
  [ "$status" -eq 0 ]
  jq -e '[.. | objects | select(.id? == "sidebar-lists") | .children | map(.id)] == [["spaces","agents","prs"]]' <<< "$output"
  jq -e '[.. | objects | select(.id? == "prs") | .content.props.view] == ["sidebar"]' <<< "$output"
}

@test "jq is idempotent on its own output" {
  once=$(jq -f "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" "$FIXTURE")
  twice=$(jq -f "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" <<< "$once")
  [ "$once" = "$twice" ]
}

@test "jq errors when sidebar-lists is missing" {
  run jq -f "$REPO_ROOT/ai/herdr/app/layouts/workbench.jq" <<< '{"id":"root"}'
  [ "$status" -ne 0 ]
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

@test "main.json without sidebar-lists warns and writes nothing" {
  mkdir -p "$APP/layouts"
  printf '%s\n' '{"id":"root"}' > "$APP/layouts/main.json"
  run _run
  [ "$status" -eq 0 ]
  [ ! -e "$APP/layouts/workbench.json" ]
  [[ "$output" == *"could not derive"* ]]
}
