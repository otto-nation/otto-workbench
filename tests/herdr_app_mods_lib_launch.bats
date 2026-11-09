#!/usr/bin/env bats
# Tests for herdr-app hc launch helpers (label contract).
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

# Evaluate EXPR against launch.js as m; print JSON.stringify(EXPR).
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/launch.js');
    process.stdout.write(JSON.stringify($1));
  "
}

@test "prLabel format" {
  _js 'm.prLabel("o/n", 7)'
  [ "$status" -eq 0 ]
  [ "$output" = '"otto.pr=o/n#7"' ]
}

@test "prKey and parsePrKey accept o/n#7 and reject o/n#x, #7, null" {
  _js '[m.prKey("o/n",7),m.parsePrKey("o/n#7"),m.parsePrKey("o/n#x"),m.parsePrKey("#7"),m.parsePrKey(null)]'
  [ "$status" -eq 0 ]
  [ "$output" = '["o/n#7",{"repo":"o/n","number":7},null,null,null]' ]
}

@test "hcRunArgv local exact" {
  _js 'm.hcRunArgv({repo:"o/n",number:7,action:"review",cwd:"/w",argv:["pr","review"]})'
  [ "$status" -eq 0 ]
  [ "$output" = '["run","--bg","--json","--cwd","/w","--label","otto.pr=o/n#7","--label","otto.action=review","--","pr","review"]' ]
}

@test "hcRunArgv with on places --on right after --cwd value and labels have no prefix" {
  _js 'm.hcRunArgv({repo:"o/n",number:7,action:"review",cwd:"/w",on:"homelab",argv:["pr","review"]})'
  [ "$status" -eq 0 ]
  [ "$output" = '["run","--bg","--json","--cwd","/w","--on","homelab","--label","otto.pr=o/n#7","--label","otto.action=review","--","pr","review"]' ]
}

@test "machineOf splits host prefix and returns null without one" {
  _js '[m.machineOf("homelab/t7"),m.machineOf("t7")]'
  [ "$status" -eq 0 ]
  [ "$output" = '["homelab",null]' ]
}

@test "terminalCommand keeps homelab/t7 verbatim" {
  _js 'm.terminalCommand("terminal.kill","homelab/t7")'
  [ "$status" -eq 0 ]
  [ "$output" = '{"name":"terminal.kill","args":{"terminal":"homelab/t7"}}' ]
}

@test "parseRunReply five cases: address, 127, failed, bad-json, no address" {
  _js '[m.parseRunReply({code:0,stdout:JSON.stringify({address:"t7"}),stderr:""}),m.parseRunReply({code:127,stdout:"",stderr:""}),m.parseRunReply({code:1,stdout:"",stderr:"boom"}),m.parseRunReply({code:0,stdout:"not-json",stderr:""}).error.kind,m.parseRunReply({code:0,stdout:"{}",stderr:""})]'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":true,"terminal":"t7"},{"ok":false,"error":{"kind":"missing-tool","message":"command not found"}},{"ok":false,"error":{"kind":"failed","message":"boom"}},"bad-json",{"ok":false,"error":{"kind":"failed","message":"no address in hc reply"}}]' ]
}
