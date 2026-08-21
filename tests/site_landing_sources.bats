#!/usr/bin/env bats
# The landing page restates data it does not own: its TIERS list names the
# component tiers that install.components and the steps.sh/setup.conf convention
# own (composed into docs/components.md from a generate-tool-context block), and
# its ITEMS list restates README's "What's Included" entries with their doc links.
# Generating the page from those sources is the thorough fix; until then this is
# the cross-validation test CLAUDE.md prescribes when one set of defaults has to
# appear in more than one format.
#
# Both lists live in app/page.tsx: the components rendering them come from
# @otto-nation/brand, so the page holds the content and nothing else. That puts
# two CardItem[] arrays in one file, which is why every parser here scopes itself
# to a named array rather than grepping the file — a bare `title:` grep would
# collect the tier titles alongside the What's Included ones.
#
# Comparing against the composed lines rather than install.components itself is
# deliberate: validate-docs-composed fails when docs/components.md drifts from
# what its .src.md composes to, so the lines cannot lag their sources. Adding a
# component therefore fails the freshness check until it is recomposed, and
# fails this suite until the landing page catches up.

setup() {
  load 'test_helper'
  common_setup
  PAGE="$REPO_ROOT/site/app/page.tsx"
  COMPONENTS_DOC="$REPO_ROOT/docs/components.md"
  README="$REPO_ROOT/README.md"
}

teardown() {
  common_teardown
}

# _array_block NAME — the lines of the `const NAME: CardItem[] = [ … ];` literal
# in app/page.tsx, so a field parser only ever sees the array it asked for.
_array_block() {
  sed -n "/^const $1: CardItem\[\] = \[/,/^\];/p" "$PAGE"
}

# _tier_items TIER — the ` · `-joined component list on TIER's TIERS entry,
# one name per line.
_tier_items() {
  local line items
  line="$(_array_block TIERS | grep -F "title: '$1'" | head -n1)"
  items="$(printf '%s' "$line" | sed -E "s/.*meta: '([^']*)'.*/\1/")"
  [ -n "$items" ] && [ "$items" != "$line" ] || return 1
  printf '%s' "$items" | sed 's/ · /|/g' | tr '|' '\n'
}

# _generated_components TIER — the backticked names on the composed
# "**Existing <tier> components:**" line of docs/components.md.
_generated_components() {
  grep -F "**Existing $1 components:**" "$COMPONENTS_DOC" \
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

# _tsx_field KEY — the single-quoted value of KEY on each entry of the array
# piped in, in order.
_tsx_field() {
  grep -oE "$1: '[^']*'" | sed -E "s/^$1: '(.*)'\$/\1/"
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
  _assert_same "page.tsx TIERS Core" \
    "$(_generated_components core)" \
    "$(_tier_items Core)"
}

@test "landing Optional tier matches the generated optional component list" {
  _assert_same "page.tsx TIERS Optional" \
    "$(_generated_components optional)" \
    "$(_tier_items Optional)"
}

@test "landing What's Included titles match the README section" {
  _assert_same "page.tsx ITEMS titles" \
    "$(_readme_included_titles)" \
    "$(_array_block ITEMS | _tsx_field title)"
}

@test "landing What's Included links match the README section" {
  _assert_same "page.tsx ITEMS hrefs" \
    "$(_readme_included_links)" \
    "$(_array_block ITEMS | _tsx_field href)"
}
