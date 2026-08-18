#!/usr/bin/env bats
# site/mdx/remark-doc-links.mjs rewrites two link shapes: a sibling *.md (which
# becomes a route) and a single-level ../ path (which becomes a GitHub URL).
# Any other relative form is passed through and 404s once published.

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

@test "every sibling .md link points at a real doc" {
  local broken=()
  while read -r target; do
    local slug="${target%%#*}"
    [ -f "$REPO_ROOT/docs/$slug" ] || broken+=("$target")
  done < <(grep -ohE '\]\((\./)?[A-Za-z0-9_-]+\.md(#[^)"]*)?( +"[^"]*")?\)' "$REPO_ROOT"/docs/*.md \
    | sed -E 's/^\]\((\.\/)?//; s/ +"[^"]*"\)$/)/; s/\)$//')
  [ "${#broken[@]}" -eq 0 ] || {
    echo "link target has no doc file: ${broken[*]}"
    return 1
  }
}

@test "no relative link escapes above the repo root" {
  local bad
  bad="$(grep -nE '\]\(\.\./\.\./' "$REPO_ROOT"/docs/*.md || true)"
  [ -z "$bad" ] || {
    echo "multi-level ../ links are not rewritten: $bad"
    return 1
  }
}
