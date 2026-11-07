#!/usr/bin/env bats
# Tests for _herdr_app_install_mods — copy herdr-app mods with a per-file manifest.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/steps.sh ai/herdr/app/mods/_lib/*.js ai/herdr/app/mods/team-prs/* lib/constants.sh lib/files.sh

setup() {
  load 'test_helper'
  common_setup
  . "$REPO_ROOT/lib/portable.sh"
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  APP="$HOME/.config/herdr-app"
  SRC="$BATS_TEST_TMPDIR/mods-src"
  cp -R "$REPO_ROOT/ai/herdr/app/mods" "$SRC"
  unset HERDR_ENV HERDR_PANE_ID
  narrow_path_to bash
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
    HERDR_APP_MODS_SRC_DIR="$3"
    _herdr_app_install_mods "$2"
  ' _ "$REPO_ROOT" "$APP" "$SRC"
}

_cksum() {
  cksum "$1" | awk '{print $1}'
}

@test "fresh install copies mod files and _lib and writes manifest" {
  run _run
  [ "$status" -eq 0 ]
  [ -f "$APP/mods/team-prs/main.js" ]
  [ -f "$APP/mods/team-prs/mod.json" ]
  [ -f "$APP/mods/team-prs/_lib/location.js" ]
  [ -f "$APP/mods/team-prs/.workbench-install" ]
  cmp -s "$SRC/team-prs/main.js" "$APP/mods/team-prs/main.js"
  cmp -s "$SRC/team-prs/mod.json" "$APP/mods/team-prs/mod.json"
  cmp -s "$SRC/_lib/location.js" "$APP/mods/team-prs/_lib/location.js"
  grep -qx $'main.js\t'"$(_cksum "$APP/mods/team-prs/main.js")" "$APP/mods/team-prs/.workbench-install"
  grep -qx $'mod.json\t'"$(_cksum "$APP/mods/team-prs/mod.json")" "$APP/mods/team-prs/.workbench-install"
  grep -qx $'_lib/location.js\t'"$(_cksum "$APP/mods/team-prs/_lib/location.js")" "$APP/mods/team-prs/.workbench-install"
  [[ "$output" == *"herdr-app mod team-prs"* ]]
}

@test "re-run is a quiet no-op with WORKBENCH_SYNC=true" {
  run _run
  [ "$status" -eq 0 ]
  WORKBENCH_SYNC=true run _run
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "changed source file rewrites that file only" {
  run _run
  [ "$status" -eq 0 ]
  orig_mod=$(cat "$APP/mods/team-prs/mod.json")
  orig_lib=$(cat "$APP/mods/team-prs/_lib/location.js")
  printf '%s\n' 'export default class TeamPrs {}' > "$SRC/team-prs/main.js"
  run _run
  [ "$status" -eq 0 ]
  cmp -s "$SRC/team-prs/main.js" "$APP/mods/team-prs/main.js"
  [ "$(cat "$APP/mods/team-prs/mod.json")" = "$orig_mod" ]
  [ "$(cat "$APP/mods/team-prs/_lib/location.js")" = "$orig_lib" ]
}

@test "target jsconfig.json and gpui-kit.d.ts then re-run is a quiet no-op" {
  run _run
  [ "$status" -eq 0 ]
  printf '%s\n' '{"compilerOptions":{}}' > "$APP/mods/team-prs/jsconfig.json"
  printf '%s\n' 'declare const kit: unknown;' > "$APP/mods/team-prs/gpui-kit.d.ts"
  WORKBENCH_SYNC=true run _run
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -f "$APP/mods/team-prs/jsconfig.json" ]
  [ -f "$APP/mods/team-prs/gpui-kit.d.ts" ]
}

@test "hand-edited file is warned and kept" {
  run _run
  [ "$status" -eq 0 ]
  printf '%s\n' 'edited' > "$APP/mods/team-prs/main.js"
  run _run
  [ "$status" -eq 0 ]
  [ "$(cat "$APP/mods/team-prs/main.js")" = "edited" ]
  [[ "$output" == *"left as is"* ]]
  grep -qx $'main.js\t'"$(_cksum "$SRC/team-prs/main.js")" "$APP/mods/team-prs/.workbench-install"
}

@test "_lib dir is not installed as a mod of its own" {
  run _run
  [ "$status" -eq 0 ]
  [ ! -e "$APP/mods/_lib" ]
}
