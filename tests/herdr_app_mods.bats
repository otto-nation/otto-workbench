#!/usr/bin/env bats
# Tests for _herdr_app_install_mods — copy herdr-app mods with a per-file manifest.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/mods_install.sh ai/herdr/steps.sh ai/herdr/app/mods/_lib/*.js ai/herdr/app/mods/_ui/*.js ai/herdr/app/mods/team-prs/* ai/herdr/app/mods/prs-button/* lib/constants.sh lib/files.sh

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
  BIN="$BATS_TEST_TMPDIR/narrow-bin"
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
    HERDR_APP_MODS_SRC_DIR="$3"
    _herdr_app_install_mods "$2"
  ' _ "$REPO_ROOT" "$APP" "$SRC"
}

_stub_app() {
  printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN/herdr-app"
  chmod +x "$BIN/herdr-app"
}

_run_step() {
  bash -c '
    set -e
    export NO_COLOR=1
    WORKBENCH_DIR="$1"
    export WORKBENCH_STABLE_DIR="$1"
    . "$1/lib/ui.sh"
    . "$1/ai/herdr/steps.sh"
    HERDR_APP_MODS_SRC_DIR="$2"
    HERDR_UNTRUSTED_APP_DIR="$3"
    step_herdr_app_mods
  ' _ "$REPO_ROOT" "$SRC" "$UNTRUSTED"
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

@test "every shared _* dir is copied into each mod and none is installed as a mod" {
  mkdir -p "$SRC/_extra"
  printf 'export const x = 1;\n' > "$SRC/_extra/x.js"
  run _run
  [ "$status" -eq 0 ]
  cmp -s "$SRC/_ui/ui.js" "$APP/mods/team-prs/_ui/ui.js"
  cmp -s "$SRC/_extra/x.js" "$APP/mods/team-prs/_extra/x.js"
  grep -q $'^_ui/ui.js\t' "$APP/mods/team-prs/.workbench-install"
  [ ! -e "$APP/mods/_ui" ]
  [ ! -e "$APP/mods/_extra" ]
}

@test "source file deleted upstream is removed from the target" {
  run _run
  [ "$status" -eq 0 ]
  rm -f "$SRC/team-prs/mod.json"
  run _run
  [ "$status" -eq 0 ]
  [ ! -e "$APP/mods/team-prs/mod.json" ]
  [ -f "$APP/mods/team-prs/main.js" ]
  run grep $'^mod.json\t' "$APP/mods/team-prs/.workbench-install"
  [ "$status" -ne 0 ]
}

@test "manifest-less identical target is adopted without rewrite" {
  mkdir -p "$APP/mods"
  cp -R "$SRC/team-prs" "$APP/mods/team-prs"
  cp -R "$SRC/_lib" "$APP/mods/team-prs/_lib"
  cp -R "$SRC/_ui" "$APP/mods/team-prs/_ui"
  before_main=$(_cksum "$APP/mods/team-prs/main.js")
  run _run
  [ "$status" -eq 0 ]
  [ -f "$APP/mods/team-prs/.workbench-install" ]
  [ "$(_cksum "$APP/mods/team-prs/main.js")" = "$before_main" ]
  grep -qx $'main.js\t'"$before_main" "$APP/mods/team-prs/.workbench-install"
  [[ "$output" == *"herdr-app mod team-prs"* ]]
  [[ "$output" != *"left as is"* ]]
}

@test "manifest-less different target is left alone with a warning" {
  mkdir -p "$APP/mods/team-prs"
  printf '%s\n' 'mine' > "$APP/mods/team-prs/main.js"
  run _run
  [ "$status" -eq 0 ]
  [ "$(cat "$APP/mods/team-prs/main.js")" = "mine" ]
  [ ! -e "$APP/mods/team-prs/.workbench-install" ]
  [[ "$output" == *"left as is"* ]]
}

@test "symlinked target is left alone with a warning" {
  mkdir -p "$BATS_TEST_TMPDIR/elsewhere" "$APP/mods"
  ln -s "$BATS_TEST_TMPDIR/elsewhere" "$APP/mods/team-prs"
  run _run
  [ "$status" -eq 0 ]
  [ -L "$APP/mods/team-prs" ]
  [ "$(readlink "$APP/mods/team-prs")" = "$BATS_TEST_TMPDIR/elsewhere" ]
  [[ "$output" == *"symlink"* ]]
  [[ "$output" == *"left as is"* ]]
}

@test "installer never copies gpui-kit.d.ts or jsconfig.json from source" {
  printf '%s\n' 'declare const kit: unknown;' > "$SRC/team-prs/gpui-kit.d.ts"
  printf '%s\n' '{"compilerOptions":{}}' > "$SRC/team-prs/jsconfig.json"
  run _run
  [ "$status" -eq 0 ]
  [ ! -e "$APP/mods/team-prs/gpui-kit.d.ts" ]
  [ ! -e "$APP/mods/team-prs/jsconfig.json" ]
  run grep 'gpui-kit.d.ts' "$APP/mods/team-prs/.workbench-install"
  [ "$status" -ne 0 ]
  run grep 'jsconfig.json' "$APP/mods/team-prs/.workbench-install"
  [ "$status" -ne 0 ]
}

@test "step is a no-op when herdr-app is absent" {
  run _run_step
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ ! -e "$APP" ]
}

@test "step installs the same mods into an existing untrusted app dir" {
  _stub_app
  mkdir -p "$UNTRUSTED/mods"
  printf '%s\n' keep > "$UNTRUSTED/mods/KEEP"
  run _run_step
  [ "$status" -eq 0 ]
  [ "$(cat "$UNTRUSTED/mods/KEEP")" = keep ]
  [ -f "$UNTRUSTED/mods/team-prs/.workbench-install" ]
  cmp "$APP/mods/team-prs/main.js" "$UNTRUSTED/mods/team-prs/main.js"
}

@test "step does not create the untrusted app dir" {
  _stub_app
  run _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$UNTRUSTED" ]
}

@test "step prints the key-binding suggestion on first install only" {
  _stub_app
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" == *'herdr-app: bind a key to open the PR board by adding to keys.json: "<key>": {"command": "tab.new", "args": {"name": "PRs", "mod": "team-prs"}}'* ]]
  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" != *'bind a key to open the PR board'* ]]
}

@test "WORKBENCH_SYNC=true re-run of the step prints nothing" {
  _stub_app
  run _run_step
  [ "$status" -eq 0 ]
  WORKBENCH_SYNC=true run _run_step
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}
