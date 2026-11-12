#!/usr/bin/env bats
# Tests for herdr-app GitHub argv builders for file-list and patch fetches.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

_js() {
  run node --input-type=module -e "const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js'); process.stdout.write(JSON.stringify($1))"
}


@test "filesArgv pages a patch-free field list" {
  _js 'm.filesArgv("acme/box", 7)'
  [ "$status" -eq 0 ]
  [[ "$output" == *'"/repos/acme/box/pulls/7/files?per_page=100"'* ]]
  [[ "$output" == *'"--paginate"'* ]]
  [[ "$output" == *'"--jq"'* ]]
  [[ "$output" == *'has_patch'* ]]
  [[ "$output" != *'"--slurp"'* ]]
}

@test "filesArgv's filter drops patches and keeps has_patch" {
  filter=$(node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    const a = m.filesArgv('o/n', 7); process.stdout.write(a[a.indexOf('--jq') + 1]);
  ")
  run jq -c "$filter" <<<'[{"filename":"a.js","status":"added","additions":1,"deletions":0,"patch":"@@"},{"filename":"b.bin","status":"modified"}]'
  [ "$status" -eq 0 ]
  [[ "$output" != *'"patch":'* ]]
  [[ "$output" == *'"filename":"a.js"'*'"has_patch":true'* ]]
  [[ "$output" == *'"filename":"b.bin"'*'"has_patch":false'* ]]
}

@test "patchArgv reads one page and selects one file" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    const a = m.patchArgv('acme/box', 7, 3, process.argv[1]);
    const want = '.[] | select(.filename == ' + JSON.stringify(process.argv[1]) + ') | .patch // empty';
    process.stdout.write(String(a[1].includes('files?per_page=100&page=3') && a[3] === want));
  " -- $'a "b".js'
  [ "$status" -eq 0 ]
  [ "$output" = "true" ]
}

@test "patchArgv's filter runs in jq" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    const a = m.patchArgv('o/n', 1, 1, 'src/x.js');
    process.stdout.write(a[a.indexOf('--jq') + 1]);
  "
  [ "$status" -eq 0 ]
  filter="$output"
  run jq -r "$filter" <<<'[{"filename":"src/y.js","patch":"no"},{"filename":"src/x.js","patch":"@@ -1 +1 @@"}]'
  [ "$status" -eq 0 ]
  [ "$output" = '@@ -1 +1 @@' ]
  run jq -r "$filter" <<<'[{"filename":"src/x.js","patch":null}]'
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

