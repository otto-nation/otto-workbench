#!/usr/bin/env bats
# The landing page restates data it does not own: how-it-works.tsx lists the
# component tiers that install.components and the steps.sh/setup.conf convention
# own (rendered into docs/components.md by generate-tool-context), and
# included.tsx restates README's "What's Included" entries with their doc links.
# Generating the page from those sources is the thorough fix; until then this is
# the cross-validation test CLAUDE.md prescribes when one set of defaults has to
# appear in more than one format.
#
# Comparing against the generated regions rather than install.components itself
# is deliberate: ci.yml already re-runs generate-tool-context and fails on a
# dirty tree, so the regions cannot lag their sources. Adding a component
# therefore fails the freshness check until it is regenerated, and fails this
# suite until the landing page catches up.

setup() {
  load 'test_helper'
  common_setup
  HOW_IT_WORKS="$REPO_ROOT/site/components/landing/how-it-works.tsx"
  INCLUDED="$REPO_ROOT/site/components/landing/included.tsx"
  COMPONENTS_DOC="$REPO_ROOT/docs/components.md"
  README="$REPO_ROOT/README.md"
}

teardown() {
  common_teardown
}

# _tier_items TIER — the ` · `-joined component list on TIER's TIERS entry,
# one name per line.
_tier_items() {
  local line items
  line="$(grep -F "title: '$1'" "$HOW_IT_WORKS" | head -n1)"
  items="$(printf '%s' "$line" | sed -E "s/.*items: '([^']*)'.*/\1/")"
  [ -n "$items" ] && [ "$items" != "$line" ] || return 1
  printf '%s' "$items" | sed 's/ · /|/g' | tr '|' '\n'
}

# _generated_components REGION — the backticked names inside a generated
# CORE-COMPONENTS / OPTIONAL-COMPONENTS region of docs/components.md.
_generated_components() {
  sed -n "/<!-- $1-START -->/,/<!-- $1-END -->/p" "$COMPONENTS_DOC" \
    | grep -oE '`[a-z][a-z-]*`' | tr -d '`'
}

# _readme_included_bullets — the `- **…**` lines of README's What's Included list.
_readme_included_bullets() {
  sed -n "/^## What's Included\$/,/^## /p" "$README" | grep '^- \*\*'
}

# _readme_included_titles — the bolded label of each bullet, link markup stripped.
_readme_included_titles() {
  _readme_included_bullets | sed -E 's/^- \*\*//; s/\*\*.*//; s/^\[([^]]*)\].*/\1/'
}

# _readme_included_links — each bullet's first doc link, as the route the site
# serves it at (`docs/tools.md#scripts` -> `/docs/tools#scripts`).
_readme_included_links() {
  local bullet target
  while IFS= read -r bullet; do
    target="$(printf '%s' "$bullet" | grep -oE '\]\(docs/[^)]*\)' | head -n1)"
    printf '%s' "$target" | sed -E 's/^\]\(//; s/\)$//; s#^docs/#/docs/#; s/\.md//'
    printf '\n'
  done < <(_readme_included_bullets)
}

# _tsx_field FILE KEY — the single-quoted value of KEY on each entry, in order.
_tsx_field() {
  grep -oE "$2: '[^']*'" "$1" | sed -E "s/^$2: '(.*)'\$/\1/"
}

_assert_same() {
  local label="$1" expected="$2" actual="$3"
  [ -n "$expected" ] || {
    echo "$label: parsed nothing from the source of truth — fix the parser"
    return 1
  }
  [ -n "$actual" ] || {
    echo "$label: parsed nothing from the landing page — fix the parser"
    return 1
  }
  [ "$expected" = "$actual" ] || {
    echo "$label diverged from its source:"
    diff <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") | sed 's/^/  /'
    return 1
  }
}

@test "landing Core tier matches the generated core component list" {
  _assert_same "how-it-works.tsx Core tier" \
    "$(_generated_components CORE-COMPONENTS)" \
    "$(_tier_items Core)"
}

@test "landing Optional tier matches the generated optional component list" {
  _assert_same "how-it-works.tsx Optional tier" \
    "$(_generated_components OPTIONAL-COMPONENTS)" \
    "$(_tier_items Optional)"
}

@test "landing What's Included titles match the README section" {
  _assert_same "included.tsx titles" \
    "$(_readme_included_titles)" \
    "$(_tsx_field "$INCLUDED" title)"
}

@test "landing What's Included links match the README section" {
  _assert_same "included.tsx hrefs" \
    "$(_readme_included_links)" \
    "$(_tsx_field "$INCLUDED" href)"
}
