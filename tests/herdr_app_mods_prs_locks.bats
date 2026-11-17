#!/usr/bin/env bats
# Locked PRs: Mine selection, Batch rows, and detail fix buttons follow the shared block rules.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/prs/*.js ai/herdr/app/mods/_lib/*.js ai/herdr/app/mods/_ui/*.js tests/herdr_app_host_helper.bash

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_app_host_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  _host_setup
}
teardown() { common_teardown; }

# __gpuiRecord does not store .tooltip(); disabled is click === null (control() skips on_click).
LOCK_JS="$(cat <<'LOCK_JS'
const ids = (rec) => rec.filter((e) => e.id).map((e) => ({
  id: e.id, click: e.click != null,
  kids: e.children.filter((c) => typeof c === "string"),
}));
const hasText = (rec, s) => rec.some((e) => e.children.includes(s));
LOCK_JS
)"

@test "detail: a locked PR disables every fix; View on GitHub, Copy link, Open session stay on" {
  _host "$LOCK_JS
    const { renderActionBar, renderDetailHeader } = await import(M + '/detail.js');
    const { fixesFor } = await import(M + '/_lib/pr_fixes.js');
    const pr = {
      isMine: true, state: 'OPEN', repoSlug: 'a/b', number: 1, title: 't', author: 'me',
      branch: 'feat', url: 'https://github.com/a/b/pull/1', cwd: '/w',
      lock: 'Locked · pr review',
      issues: [{ kind: 'conflicts' }, { kind: 'ci-failing' }],
    };
    const fixes = fixesFor(pr);
    globalThis.__gpuiRecord = [];
    renderActionBar([], { fixes });
    const bar = ids(globalThis.__gpuiRecord);
    const fix = (id) => bar.find((e) => e.id === id);
    globalThis.__gpuiRecord = [];
    renderDetailHeader({
      copy: [{ key: 'link', label: 'Link', value: pr.url }],
      link: pr.url, generated: 0,
      presentation: {
        ref: 'a/b#1', title: 't', author: 'me', branch: 'feat', base_branch: '',
        stats: { additions: '+0', deletions: '-0', files: '0 files' },
        tags: [], ci: null, decision: null, detail_issues: [], review: null, busy: null,
      },
    }, {
      onClose: () => {}, onOpenUrl: () => {}, onReview: () => {}, onCopy: () => {},
      reviewState: { enabled: true, reason: null },
      targetSession: { gate: 'ok', onOpen: () => {} },
    });
    const head = ids(globalThis.__gpuiRecord);
    const on = (id) => head.find((e) => e.id === id)?.click === true;
    out({
      reasons: fixes.map((f) => [f.id, f.enabled, f.reason]),
      rebase: fix('fix-rebase'),
      ci: fix('fix-ci'),
      nav: { github: on('detail-open'), copy: on('copy-link'), session: on('open-session-target') },
    });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"reasons":[["fix-rebase",false,"Locked · pr review"],["fix-ci",false,"Locked · pr review"]],"rebase":{"id":"fix-rebase","click":false,"kids":["Rebase"]},"ci":{"id":"fix-ci","click":false,"kids":["Fix CI"]},"nav":{"github":true,"copy":true,"session":true}}' ]
}

@test "batch page: a PR with cwd and a lock has no checkbox and the lock caption" {
  _host "$LOCK_JS
    const { renderBatchPage } = await import(M + '/batch_page.js');
    const pr = {
      repoSlug: 'a/b', number: 1, title: 'Locked PR', branch: 'feat', cwd: '/w',
      lock: 'Locked · pr review',
    };
    const launcher = {
      shown: (prs) => prs, selected: new Set(), steps: new Set(), withAi: new Set(),
      autoPublish: false, rebaseBehind: false, starting: false, note: null,
      models: { render: () => null },
      filters: { get: () => ({}), toggle: () => {} },
      toggle() {}, toggleRepo() {}, toggleStep() {}, toggleAi() {},
      toggleRebaseBehind() {}, toggleAutoPublish() {}, reset() {}, canClear: () => false, launch() {},
    };
    globalThis.__gpuiRecord = [];
    renderBatchPage({
      launcher, prs: [pr],
      workflows: { activeRuns: () => [], barState: () => ({ runs: [], error: null }) },
      onBack: () => {}, onOpenPr: () => {}, run: () => {},
      filterBar: { render: () => null },
    });
    const rec = globalThis.__gpuiRecord;
    const row = rec.find((e) => e.id === 'pr-a/b#1');
    out({ click: row.click != null, caption: hasText(rec, 'Locked · pr review') });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"click":false,"caption":true}' ]
}

@test "selection: can is false for a PR with a lock" {
  _host "
    const fs = await import('node:fs');
    const doc = JSON.parse(fs.readFileSync(FIX, 'utf8'));
    H.exec = fakePr(doc);
    const { default: PrsBoard } = await import(M + '/board_view.js');
    const b = new PrsBoard();
    const host = fakeCx();
    b.init({}, host.cx);
    await host.settle();
    b.startSelecting();
    const s = b.selection();
    const open = { state: 'OPEN', repoSlug: 'a/b', number: 2, cwd: '/w' };
    out({
      locked: s.can({ ...open, number: 1, lock: 'Locked · pr review' }),
      open: s.can(open),
    });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"locked":false,"open":true}' ]
}
