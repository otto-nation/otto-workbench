#!/usr/bin/env bats
# Tests for ai/herdr/web/lib/targets.js — board scope from herdr-core terminals.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/* ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_web_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}
teardown() { common_teardown; }

@test "terminalTargets: one per (machine, cwd), in hc ls order, live only, remote addresses keep their machine" {
  _web "const m = await import(W + '/lib/targets.js');
        out([m.lsAllArgv(), m.terminalTargets({ terminals: [
          { address: 't8', cwd: '/home/a/git/x', state: 'live' },
          { address: 't9', cwd: '/home/a/git/x', state: 'live' },
          { address: 't10', cwd: '/home/a/git/y', state: 'finished' },
          { address: 't11', state: 'live' },
          { address: 'homelab/t2', cwd: '/home/a/git/x', state: 'live' },
          { address: 't12', cwd: '', state: 'live' },
          { address: 't13', cwd: '/home/a/git/z' } ] })]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[["ls","--json"],[{"on":null,"cwd":"/home/a/git/x"},{"on":"homelab","cwd":"/home/a/git/x"},{"on":null,"cwd":"/home/a/git/z"}]]' ]
}

@test "terminalTargets: junk input is an empty list" {
  _web "const m = await import(W + '/lib/targets.js');
        out([m.terminalTargets(null), m.terminalTargets({}), m.terminalTargets([{ address: 't1', cwd: '/a' }]), m.terminalTargets({ terminals: [null, 5] })]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[[],[],[{"on":null,"cwd":"/a"}],[]]' ]
}
