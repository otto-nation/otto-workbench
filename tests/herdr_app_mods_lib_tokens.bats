#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/tokens.js — breakpoints and layout tokens.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() { load 'test_helper'; common_setup; }
teardown() { common_teardown; }

@test "tokens: breakpoints and layout tokens exported" {
  run node --input-type=module -e "
    const t = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/tokens.js');
    process.stdout.write(JSON.stringify({b:t.BREAKPOINT,l:t.LAYOUT,d:typeof t.densityOf}));
  "
  [ "$status" -eq 0 ]
  jq -e '.b.compact==560 and .l.colMin==260 and .l.headerH==52 and .d=="undefined"' <<<"$output"
}

@test "tokens: spacing, radii and the compact breakpoint follow the brand module" {
  local lib="$BATS_TEST_TMPDIR/_lib"
  mkdir -p "$lib"
  cp "$REPO_ROOT/ai/herdr/app/mods/_lib/tokens.js" "$lib/"
  # A brand module whose values differ from today's, so a literal left in tokens.js shows.
  node --input-type=module -e "
    const { BRAND } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/brand.generated.js');
    const b = structuredClone(BRAND);
    b.space = { 1: 5, 2: 9, 3: 13, 4: 17, 5: 21, 6: 25 };
    b.radius = { sm: 3, md: 7, lg: 9 };
    b.pane = { compact: 150, board: 600, wide: 1200 };
    process.stdout.write('export const BRAND = ' + JSON.stringify(b) + ';\n');
  " > "$lib/brand.generated.js"
  run node --input-type=module -e "
    const t = await import('$lib/tokens.js');
    process.stdout.write(JSON.stringify({ sp: t.SP, r: t.RADIUS, c: t.BREAKPOINT.compact }));
  "
  [ "$status" -eq 0 ]
  jq -e '.sp.xs==5 and .sp.sm==9 and .sp.md==13 and .sp.lg==17 and .sp.xl==25 and .sp.hair==1 and .sp.xxs==2
    and .r.tag==3 and .r.control==7 and .r.card==9 and .c==600' <<<"$output"
}
