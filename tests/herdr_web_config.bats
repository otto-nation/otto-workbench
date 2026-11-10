#!/usr/bin/env bats
# Tests for ai/herdr/web/server/config.js — the PR board's environment contract (spec § 8.3).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/* ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_web_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}
teardown() { common_teardown; }

TOK='0123456789abcdef0123456789abcdef'

@test "config: minimal env gives the documented defaults" {
  _web "const { loadConfig } = await import(W + '/server/config.js');
        out(loadConfig({ PR_BOARD_TOKEN: '$TOK', PR_BOARD_PORT: '7330', HOME: '/home/a' }));"
  [ "$status" -eq 0 ]
  jq -e '.host=="127.0.0.1" and .port==7330 and .hcBin=="hc" and .name=="this server"
         and .stateDir=="/home/a/.local/state/pr-board" and .fullMs==180000 and .listingMs==15000
         and .maxProcs==4 and .origin==null and .ovisUrl==null' <<<"$output"
}

@test "config: XDG_STATE_HOME and PR_BOARD_STATE_DIR pick the state dir; empty counts as unset" {
  _web "const { loadConfig } = await import(W + '/server/config.js');
        const b = { PR_BOARD_TOKEN: '$TOK', PR_BOARD_PORT: '1', HOME: '/h' };
        out([loadConfig({ ...b, XDG_STATE_HOME: '/x' }).stateDir,
             loadConfig({ ...b, XDG_STATE_HOME: '/x', PR_BOARD_STATE_DIR: '/s' }).stateDir,
             loadConfig({ ...b, XDG_STATE_HOME: '', PR_BOARD_STATE_DIR: '' }).stateDir,
             loadConfig({ ...b, PR_BOARD_HOST: '' }).host, loadConfig({ ...b, PR_BOARD_HC: '' }).hcBin]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["/x/pr-board","/s","/h/.local/state/pr-board","127.0.0.1","hc"]' ]
}

@test "config: refuses short token, unset or empty token, bad port, non-loopback host, origin with a path" {
  _web "const { loadConfig, ConfigError } = await import(W + '/server/config.js');
        const b = { PR_BOARD_TOKEN: '$TOK', PR_BOARD_PORT: '7330', HOME: '/h' };
        const why = (e) => { try { loadConfig(e); return 'ok'; } catch (x) { return (x instanceof ConfigError ? 'CE:' : 'X:') + x.message.split(' ')[0]; } };
        out([why({ ...b, PR_BOARD_TOKEN: 'short' }), why({ ...b, PR_BOARD_TOKEN: undefined }), why({ ...b, PR_BOARD_TOKEN: '' }),
             why({ ...b, PR_BOARD_PORT: '' }), why({ ...b, PR_BOARD_PORT: '70000' }), why({ ...b, PR_BOARD_PORT: '7a' }),
             why({ ...b, PR_BOARD_HOST: '0.0.0.0' }), why({ ...b, PR_BOARD_HOST: '::1' }),
             why({ ...b, PR_BOARD_ORIGIN: 'https://x.lab/path' }), why({ ...b, PR_BOARD_ORIGIN: 'https://prs.lab.home' })]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["CE:PR_BOARD_TOKEN","CE:PR_BOARD_TOKEN","CE:PR_BOARD_TOKEN","CE:PR_BOARD_PORT","CE:PR_BOARD_PORT","CE:PR_BOARD_PORT","CE:PR_BOARD_HOST","ok","CE:PR_BOARD_ORIGIN","ok"]' ]
}

@test "config: interval and process overrides are seconds and bounded" {
  _web "const { loadConfig } = await import(W + '/server/config.js');
        const c = loadConfig({ PR_BOARD_TOKEN: '$TOK', PR_BOARD_PORT: '0', HOME: '/h', PR_BOARD_FULL_REFRESH_S: '60',
          PR_BOARD_LISTING_REFRESH_S: '5', PR_BOARD_MAX_PROCS: '2', PR_BOARD_NAME: 'homelab · maximum', PR_BOARD_HC: '/h/.local/bin/herdr-core' });
        out([c.fullMs, c.listingMs, c.maxProcs, c.name, c.hcBin, c.port, Object.isFrozen(c)]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[60000,5000,2,"homelab · maximum","/h/.local/bin/herdr-core",0,true]' ]
}
