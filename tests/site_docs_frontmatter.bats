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

@test "every docs/*.md has a non-empty title" {
  local missing=()
  for f in "$REPO_ROOT"/docs/*.md; do
    grep -qE '^title: +\S' "$f" || missing+=("$(basename "$f")")
  done
  [ "${#missing[@]}" -eq 0 ] || {
    echo "missing title: ${missing[*]}"
    return 1
  }
}

@test "every docs/*.md has a non-empty description" {
  local missing=()
  for f in "$REPO_ROOT"/docs/*.md; do
    grep -qE '^description: +\S' "$f" || missing+=("$(basename "$f")")
  done
  [ "${#missing[@]}" -eq 0 ] || {
    echo "missing description: ${missing[*]}"
    return 1
  }
}
