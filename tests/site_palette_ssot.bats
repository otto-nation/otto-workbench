#!/usr/bin/env bats
# Before the brand extraction, site/app/tokens.css was this repo's palette owner
# and this suite kept components from growing a second one. The owner is now
# @otto-nation/brand/tokens.css, so the rule is strictly stronger: no hex belongs
# anywhere under site/ at all, app/ included.
#
# app/icon.svg is the one exemption. Next's metadata convention reads it off disk
# as a static file, so it cannot resolve a CSS custom property — site/icon-parity.test.mjs
# is what keeps it from drifting from the mark it copies.
#
# The companion check — that every --ow-* a component references is declared —
# moved into otto-brand-check, which ships with the package and reads the installed
# tokens.css. A bats test cannot: after migration that file lives in node_modules.

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

@test "no site source carries a hex color literal" {
  local offenders
  offenders="$(grep -rnE '#[0-9a-fA-F]{3,8}\b' \
    "$REPO_ROOT/site/app" "$REPO_ROOT/site/lib" "$REPO_ROOT/site/mdx" \
    --include='*.tsx' --include='*.ts' --include='*.css' --include='*.mjs' \
    || true)"
  [ -z "$offenders" ] || {
    echo "hex literals under site/ — the palette lives in @otto-nation/brand:"
    printf '%s\n' "$offenders" | sed 's/^/  /'
    return 1
  }
}

@test "the palette is not re-declared locally" {
  local offenders
  # --include must precede the `--`; after it grep reads the flag as a filename
  # and the filter silently never applies.
  offenders="$(grep -rln --include='*.css' -- '--ow-[a-z-]*:' \
    "$REPO_ROOT/site/app" "$REPO_ROOT/site/lib" "$REPO_ROOT/site/mdx" \
    || true)"
  [ -z "$offenders" ] || {
    echo "these files declare --ow-* tokens; @otto-nation/brand owns them:"
    printf '%s\n' "$offenders" | sed 's/^/  /'
    return 1
  }
}
