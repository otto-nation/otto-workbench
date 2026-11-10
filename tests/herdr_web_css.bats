#!/usr/bin/env bats
# Tests for ai/herdr/web/lib/css.js — theme JSON, tokens and brand → CSS (spec § 4.5, § 6.7).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/* ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_web_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}
teardown() { common_teardown; }

@test "css: theme variables come from the committed otto theme JSON, light on :root, dark on data-theme and prefers-color-scheme" {
  _web "const c = await import(W + '/lib/css.js');
        const fs = await import('node:fs');
        const j = (n) => JSON.parse(fs.readFileSync(W + '/../app/themes/' + n + '.generated.json', 'utf8'));
        const light = j('otto-light'), dark = j('otto-dark');
        const css = c.themeCss(light, dark);
        out({ rootCanvas: css.split('[data-theme=\"dark\"]')[0].includes('--canvas:' + light.colors.canvas),
              darkCanvas: css.includes('[data-theme=\"dark\"]{color-scheme:dark;') && css.includes('--canvas:' + dark.colors.canvas),
              auto: css.includes('@media (prefers-color-scheme: dark){:root:not([data-theme])'),
              ansi1: css.includes('--ansi1:' + dark.ansi[1]), underscore: css.includes('--accent-foreground:'), noUnderscore: !css.includes('accent_foreground'),
              status: ['--status-pass:var(--done)', '--status-fail:var(--ansi1)', '--status-running:var(--working)', '--status-warn:var(--ansi3)', '--status-none:var(--muted)'].every((v) => css.includes(v)),
              scrim: css.includes('--scrim:color-mix(in srgb, var(--foreground) 50%, transparent)') && css.includes('--scrim:color-mix(in srgb, var(--canvas) 70%, transparent)') });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"rootCanvas":true,"darkCanvas":true,"auto":true,"ansi1":true,"underscore":true,"noUnderscore":true,"status":true,"scrim":true}' ]
}

@test "css: sizes, type roles and fills come from _lib/tokens.js; breakpoints from tokens and the brand module" {
  _web "const c = await import(W + '/lib/css.js');
        const t = await import(L + '/tokens.js');
        const { BRAND } = await import(L + '/brand.generated.js');
        const css = c.tokensCss(t);
        const font = (r, fam) => '--font-' + r + ':' + t.TYPE[r].weight + ' ' + t.TYPE[r].size + 'px/' + t.TYPE[r].lh + 'px var(' + fam + ')';
        const want = ['--sp-sm:' + t.SP.sm + 'px', '--radius-tag:' + t.RADIUS.tag + 'px', '--radius-card:' + t.RADIUS.card + 'px',
          '--col-min:' + t.LAYOUT.colMin + 'px', '--control-h:' + t.CONTROL.h + 'px', '--control-h-dense:' + t.CONTROL.hDense + 'px',
          '--control-px:' + t.CONTROL.px + 'px', '--row-h-list:' + t.ROW_H.list + 'px', '--row-h-diff:' + t.ROW_H.diff + 'px',
          '--fill-lane:' + c.fillPercent(t.FILL.lane) + '%', '--filter-label-w:' + t.LAYOUT.filterLabelW + 'px',
          font('body', '--font-sans'), font('title', '--font-sans'), font('code', '--font-mono'), '--rule-selected:2px', '--focus-width:2px'];
        const m = c.mediaQueries(t, BRAND);
        out([want.filter((v) => !css.includes(v)), !/--text-|--control-h-compact/.test(css),
             m.compact === '(max-width: ' + (t.BREAKPOINT.compact - 0.02) + 'px)', m.wide === '(min-width: ' + BRAND.pane.wide + 'px)', m.wrap, c.fillPercent('55')]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[[],true,true,true,"(max-width: 760px)",33]' ]
}
