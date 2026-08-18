#!/usr/bin/env bats
# Every doc rendered by the site needs title and description frontmatter —
# without them the page tree, tab titles, and search index lose their labels.

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# _frontmatter_block FILE — prints the lines between the opening `---` and the
# next `---`, so a `title:`-prefixed line in the body (a code sample, prose)
# can't stand in for the real frontmatter field.
_frontmatter_block() {
  sed -n '2,/^---$/p' "$1" | sed '$d'
}

# _frontmatter_field FILE KEY — prints KEY's frontmatter value with any
# wrapping quotes stripped, so a quoted-empty value (`title: ""`) reads empty.
_frontmatter_field() {
  local value
  value="$(_frontmatter_block "$1" | grep -E "^$2: " | head -n1 | sed -E "s/^$2: *//")"
  value="${value%\"}"
  value="${value#\"}"
  printf '%s' "$value"
}

@test "every docs/*.md has a non-empty title" {
  local missing=()
  for f in "$REPO_ROOT"/docs/*.md; do
    [ -n "$(_frontmatter_field "$f" title)" ] || missing+=("$(basename "$f")")
  done
  [ "${#missing[@]}" -eq 0 ] || {
    echo "missing title: ${missing[*]}"
    return 1
  }
}

@test "every docs/*.md has a non-empty description" {
  local missing=()
  for f in "$REPO_ROOT"/docs/*.md; do
    [ -n "$(_frontmatter_field "$f" description)" ] || missing+=("$(basename "$f")")
  done
  [ "${#missing[@]}" -eq 0 ] || {
    echo "missing description: ${missing[*]}"
    return 1
  }
}
