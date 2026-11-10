#!/usr/bin/env bats
# Tests for ai/herdr/web/server/auth.js — session cookie, bearer, Origin, guess budget (spec § 7).
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

@test "auth: session value is an HMAC of the token, stable, and differs per token" {
  _web "const a = await import(W + '/server/auth.js');
        out([a.sessionValue('$TOK') === a.sessionValue('$TOK'), a.sessionValue('$TOK') === a.sessionValue('${TOK}x'),
             /^[0-9a-f]{64}$/.test(a.sessionValue('$TOK')), a.sessionValue('$TOK').includes('$TOK')]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[true,false,true,false]' ]
}

@test "auth: cookie or bearer gets in; wrong, raw-token cookie, or none does not" {
  _web "const a = await import(W + '/server/auth.js');
        const req = (h) => new Request('http://127.0.0.1:7330/api/board', { headers: h });
        out([a.authenticated(req({ cookie: 'x=1; pr_board_session=' + a.sessionValue('$TOK') }), '$TOK'),
             a.authenticated(req({ authorization: 'Bearer $TOK' }), '$TOK'),
             a.authenticated(req({ cookie: 'pr_board_session=$TOK' }), '$TOK'),
             a.authenticated(req({ authorization: 'Bearer nope' }), '$TOK'),
             a.authenticated(req({}), '$TOK'),
             a.hasCredential(req({})), a.hasCredential(req({ authorization: 'Bearer x' }))]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[true,true,false,false,false,false,true]' ]
}

@test "auth: the session cookie is host-only, HttpOnly, SameSite=Strict, Secure only behind https" {
  _web "const a = await import(W + '/server/auth.js');
        const https = new Request('http://127.0.0.1/', { headers: { 'x-forwarded-proto': 'https' } });
        out([a.sessionCookie('$TOK', a.isSecure(https)), a.sessionCookie('$TOK', a.isSecure(new Request('http://127.0.0.1/'))), a.clearedCookie()]);"
  [ "$status" -eq 0 ]
  jq -e '(.[0]|test("^pr_board_session=[0-9a-f]{64}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000; Secure$"))
         and (.[1]|test("Max-Age=31536000$")) and ((.[0]|test("Domain"))|not)
         and .[2]=="pr_board_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"' <<<"$output"
}

@test "auth: sameOrigin needs an Origin matching Host or the configured origin, and same-origin fetch metadata" {
  _web "const a = await import(W + '/server/auth.js');
        const r = (h) => new Request('http://127.0.0.1:7330/api/refresh', { method: 'POST', headers: { host: 'prs.lab.home', ...h } });
        out([a.sameOrigin(r({ origin: 'https://prs.lab.home' }), null),
             a.sameOrigin(r({}), null),
             a.sameOrigin(r({ origin: 'https://evil.example' }), null),
             a.sameOrigin(r({ origin: 'https://prs.lab.home', 'sec-fetch-site': 'cross-site' }), null),
             a.sameOrigin(r({ origin: 'https://prs.lab.home', 'sec-fetch-site': 'same-origin' }), 'https://prs.lab.home'),
             a.sameOrigin(r({ origin: 'http://prs.lab.home' }), 'https://prs.lab.home'),
             a.isJson(r({ 'content-type': 'application/json; charset=utf-8' })), a.isJson(r({ 'content-type': 'text/plain' }))]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[true,false,false,false,true,false,true,false]' ]
}

@test "auth: five free failures in ten minutes, then a doubling delay capped at a minute" {
  _web "const a = await import(W + '/server/auth.js');
        let t = 0; const b = a.createGuessBudget({ now: () => t });
        const d = []; for (let i = 0; i < 12; i++) { d.push(b.delayMs()); b.fail(); }
        t += 600001; const after = b.delayMs(); b.fail(); b.reset();
        out([d, after, b.delayMs()]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[[0,0,0,0,0,1000,2000,4000,8000,16000,32000,60000],0,0]' ]
}

@test "auth: guess budget does not grow without bound under many failures" {
  _web "const a = await import(W + '/server/auth.js');
        const t = 0; const free = 5; const maxDelayMs = 60000;
        const b = a.createGuessBudget({ now: () => t, free, maxDelayMs });
        const cap = free + Math.ceil(Math.log2(maxDelayMs / 1000));
        for (let i = 0; i < 100000; i++) b.fail();
        out([b.size() <= cap, b.size(), b.delayMs(), cap]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[true,11,60000,11]' ]
}
