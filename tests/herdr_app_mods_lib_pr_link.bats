#!/usr/bin/env bats
# What a PR header offers to copy, shared by the mod's detail and the web's: one list, one URL rule.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/pr_link.js ai/herdr/web/public/dom.js ai/herdr/web/public/detail_view.js ai/herdr/app/mods/prs/*.js ai/herdr/app/mods/_ui/*.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  LIB="$REPO_ROOT/ai/herdr/app/mods/_lib"
  WEB="$REPO_ROOT/ai/herdr/web"
  MODS="$REPO_ROOT/ai/herdr/app/mods"
}

teardown() {
  common_teardown
}

@test "copyFields lists the full values in order and leaves out what the PR lacks" {
  run node --input-type=module -e "
    const m = await import('$LIB/pr_link.js');
    const full = { repoSlug: 'acme/box', number: 7, branch: 'me/fix', headSha: 'abcdef1234567', url: 'https://github.com/acme/box/pull/7' };
    process.stdout.write(JSON.stringify([
      m.copyFields(full).map((f) => [f.key, f.value]),
      m.copyFields({ repoSlug: 'acme/box', number: 7 }).map((f) => [f.key, f.value]),
      m.shortSha('abcdef1234567'), m.shortSha(null),
    ]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[[["ref","acme/box#7"],["branch","me/fix"],["sha","abcdef1234567"],["link","https://github.com/acme/box/pull/7"]],[["ref","acme/box#7"],["link","https://github.com/acme/box/pull/7"]],"abcdef1",""]' ]
}

@test "the copied link is the https-only PR URL, never a javascript: or credentialed one" {
  run node --input-type=module -e "
    const m = await import('$LIB/pr_link.js');
    const link = (pr) => m.copyFields(pr).find((f) => f.key === 'link')?.value ?? null;
    process.stdout.write(JSON.stringify([
      link({ url: 'javascript:alert(1)', repoSlug: 'a/b', number: 1 }),
      link({ url: 'https://u:pw@evil/', repoSlug: 'a/b', number: 1 }),
      link({ repoSlug: 'a/b', number: 1, host: 'evil.com/steal' }),
      link({ url: 'https://github.com/a/b/pull/1' }),
    ]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '["https://github.com/a/b/pull/1","https://github.com/a/b/pull/1","https://github.com/a/b/pull/1","https://github.com/a/b/pull/1"]' ]
}

@test "web copyable: a click puts the full value on the clipboard, says Copied, then restores the text" {
  run node --input-type=module -e "
    class N {}
    class E extends N {
      constructor(tag) { super(); this.tagName = tag; this.children = []; this.attrs = {}; this.className = ''; this.listeners = {}; }
      setAttribute(k, v) { this.attrs[k] = v; }
      addEventListener(t, fn) { this.listeners[t] = fn; }
      append(...c) { this.children.push(...c); }
      replaceChildren(...c) { this.children = [...c]; }
    }
    globalThis.Node = N;
    globalThis.document = { createElement: (t) => new E(t), createTextNode: (s) => ({ text: s }) };
    const wrote = [];
    Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async (v) => { wrote.push(v); } } }, configurable: true });
    let restore = null;
    globalThis.setTimeout = (fn, ms) => { restore = [fn, ms]; };
    const { copyable } = await import('$WEB/public/dom.js');
    const { COPIED_MS } = await import('$LIB/pr_link.js');
    const el = copyable({ key: 'sha', label: 'Commit', value: 'abcdef1234567' }, 'abcdef1');
    const before = el.children.map((c) => c.text ?? c.className);
    await el.listeners.click({ stopPropagation() {} });
    const during = el.children.map((c) => c.className);
    restore[0]();
    process.stdout.write(JSON.stringify([wrote, before, during, restore[1] === COPIED_MS, el.children.map((c) => (typeof c === 'string' ? c : c.text ?? c.className)), el.attrs.title]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[["abcdef1234567"],["abcdef1"],["status pass"],true,["abcdef1"],"Copy commit"]' ]
}

@test "both surfaces take what is copyable and the URL rule from pr_link.js, and nothing else writes the clipboard" {
  grep -q "copyFields(pr)" "$MODS/prs/detail.js"
  grep -q "copyFields(pr)" "$WEB/public/detail_view.js"
  run grep -rn "function prUrl\|function httpsUrl" "$MODS" "$WEB"
  [[ "$output" == *"_lib/pr_link.js"* ]]
  [ "$(echo "$output" | wc -l | tr -d ' ')" -eq 2 ]
  # the mod writes the clipboard from one method, the web from one helper (the queue's own row copy aside)
  run grep -rln "write_to_clipboard" "$MODS/prs"
  [ "$output" = "$MODS/prs/main.js" ]
  run grep -rln "clipboard.writeText" "$WEB"
  [ "$output" = "$WEB/public/dom.js" ]
}
