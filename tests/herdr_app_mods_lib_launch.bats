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
  [ "$output" = '["run","--bg","--json","--cwd","/w","--label","otto.pr=o/n#7","--label","otto.action=review","--label","otto.app=pr","--","pr","review"]' ]
}

@test "hcRunArgv with on places --on right after --cwd value, labels have no prefix, and the command runs with the workbench bin on PATH" {
  _js 'm.hcRunArgv({repo:"o/n",number:7,action:"review",cwd:"/w",on:"homelab",argv:["pr","review"]})'
  [ "$status" -eq 0 ]
  [ "$output" = '["run","--bg","--json","--cwd","/w","--on","homelab","--label","otto.pr=o/n#7","--label","otto.action=review","--label","otto.app=pr","--","sh","-c","PATH=\"$HOME/.local/bin:$PATH\"; exec \"$@\"","sh","pr","review"]' ]
}

@test "hcBatchArgv local exact" {
  _js 'm.hcBatchArgv({repo:"o/n",cwd:"/w",argv:["pr","batch","run"]})'
  [ "$status" -eq 0 ]
  [ "$output" = '["run","--bg","--json","--cwd","/w","--label","otto.repo=o/n","--label","otto.action=batch","--label","otto.app=pr","--","pr","batch","run"]' ]
}

@test "hcBatchArgv with on places --on right after --cwd value, labels have no prefix, and the command runs with the workbench bin on PATH" {
  _js 'm.hcBatchArgv({repo:"o/n",cwd:"/w",on:"homelab",argv:["pr","batch","run"]})'
  [ "$status" -eq 0 ]
  [ "$output" = '["run","--bg","--json","--cwd","/w","--on","homelab","--label","otto.repo=o/n","--label","otto.action=batch","--label","otto.app=pr","--","sh","-c","PATH=\"$HOME/.local/bin:$PATH\"; exec \"$@\"","sh","pr","batch","run"]' ]
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

@test "outcomeText maps action and exitCode to level and title" {
  _js '[m.outcomeText("review",0),m.outcomeText("rerun",0).title,m.outcomeText("recover",1),m.outcomeText("post-approve",null),m.outcomeText("weird",0).title]'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"level":"success","title":"Review finished"},"Review finished",{"level":"error","title":"Recovery failed (exit 1)"},{"level":"error","title":"Approval ended unexpectedly"},"Action finished"]' ]
}
