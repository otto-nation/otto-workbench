#!/usr/bin/env bats
# Tests for the PR board page client: EventSource lifecycle on a tab switch.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/public/app.js ai/herdr/web/public/api.js

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_web_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}
teardown() { common_teardown; }

# app.js boot() calls render() which needs a real DOM; the fake is enough for
# boot to register the tab click and for setUi to close/open the stream (that
# runs before render). render() throwing is expected and caught.
PAGE_DOM='
        const opened = [];
        class FakeES {
          constructor(url) { this.url = url; this.closed = 0; opened.push(this); }
          addEventListener() {}
          close() { this.closed++; }
        }
        globalThis.EventSource = FakeES;
        const store = {};
        globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
        const els = {};
        const el = (id) => {
          if (els[id]) return els[id];
          const e = {
            id, className: "", hidden: false, textContent: "", title: "", disabled: false, dataset: {},
            children: [], attrs: {}, listeners: [],
            classList: { toggle() {} },
            setAttribute(k, v) { this.attrs[k] = String(v); },
            addEventListener(type, fn) { this.listeners.push([type, fn]); },
            replaceChildren() {},
            querySelectorAll() { return []; },
            closest(sel) { return sel === "[data-tab]" && this.dataset.tab ? this : null; },
          };
          els[id] = e;
          return e;
        };
        const html = el("html");
        html.dataset = {};
        globalThis.document = {
          documentElement: html,
          querySelector(s) {
            if (s === "meta[name=\"theme-color\"]") { const m = el("meta"); m.content = ""; return m; }
            if (s.startsWith("#")) return el(s.slice(1));
            return el(s);
          },
          querySelectorAll() { return []; },
          createElement() { return el("anon"); },
          createTextNode(s) { return { text: s }; },
          addEventListener() {},
        };
        globalThis.window = globalThis;
        globalThis.addEventListener = () => {};
        globalThis.location = { hash: "", reload() {} };
        globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
        globalThis.getComputedStyle = () => ({ getPropertyValue: () => "#000" });
        globalThis.fetch = async () => ({ status: 200, ok: true, json: async () => ({}) });
        globalThis.innerWidth = 1280;
        globalThis.setInterval = () => 0;
        globalThis.setTimeout = () => 0;
'

@test "client: a tab switch closes the old EventSource and opens one stream with the new view" {
  _web "$PAGE_DOM
        try { await import(W + '/public/app.js'); } catch { /* render needs a real DOM */ }
        const click = els.tabSeg.listeners.find((l) => l[0] === 'click')[1];
        const mine = el('mineBtn');
        mine.dataset.tab = 'mine';
        try { click({ target: mine }); } catch { /* setUi already closed/opened */ }
        out({ urls: opened.map((e) => e.url), closed: opened.map((e) => e.closed), n: opened.length });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"urls":["/api/events?view=team","/api/events?view=mine"],"closed":[1,0],"n":2}' ]
}
