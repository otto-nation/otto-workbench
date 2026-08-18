#!/usr/bin/env bats
# The sidebar order lives in site/lib/source.ts, not a docs/meta.json. That keeps
# site config out of docs/ but means nothing links the two — this test is the link.

setup() {
  load 'test_helper'
  common_setup
  ORDER_FILE="$REPO_ROOT/site/lib/source.ts"
}

teardown() {
  common_teardown
}

_ordered_slugs() {
  sed -n "/SIDEBAR_ORDER = \[/,/\]/p" "$ORDER_FILE" | grep -oE "['\"][a-z0-9-]+['\"]" | tr -d "'\""
}

_doc_slugs() {
  for f in "$REPO_ROOT"/docs/*.md; do
    basename "$f" .md
  done
}

@test "SIDEBAR_ORDER parses to a non-empty slug list" {
  local count
  count="$(_ordered_slugs | grep -c . || true)"
  [ "$count" -gt 0 ] || {
    echo "no slugs parsed from SIDEBAR_ORDER in $ORDER_FILE — check its quoting"
    return 1
  }
}

@test "every ordered slug has a doc file" {
  local orphans=()
  while read -r slug; do
    [ -f "$REPO_ROOT/docs/$slug.md" ] || orphans+=("$slug")
  done < <(_ordered_slugs)
  [ "${#orphans[@]}" -eq 0 ] || {
    echo "ordered but missing from docs/: ${orphans[*]}"
    return 1
  }
}

@test "every doc file is in the sidebar order" {
  local unlisted=()
  local ordered
  ordered="$(_ordered_slugs)"
  while read -r slug; do
    grep -qxF "$slug" <<< "$ordered" || unlisted+=("$slug")
  done < <(_doc_slugs)
  [ "${#unlisted[@]}" -eq 0 ] || {
    echo "in docs/ but not ordered: ${unlisted[*]}"
    return 1
  }
}
