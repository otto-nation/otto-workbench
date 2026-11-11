#!/usr/bin/env bats
# Style guards for the PR board page: the brand DESIGN.md rules a grep can see (as tests/herdr_app_mods_style.bats).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/public/* ai/herdr/web/lib/*

setup() {
  load 'test_helper'
  common_setup
  WEB="$REPO_ROOT/ai/herdr/web"
}

teardown() {
  common_teardown
}

@test "the brand forbids pills: no pill class, no 999px or 50% radius on a control" {
  run grep -rnE "class: ['\"\`][^'\"\`]*\bpill\b|\.pill\b|border-radius: *(999|9999)px|border-radius:\s*50%" "$WEB/public" "$WEB/lib"
  [ "$status" -eq 1 ]
}

@test "the spinner respects prefers-reduced-motion" {
  run grep -n "prefers-reduced-motion" "$WEB/public/style.css"
  [ "$status" -eq 0 ]
  run grep -nE "animation:\s*none" "$WEB/public/style.css"
  [ "$status" -eq 0 ]
}

@test "the fail glyph is ×, never ✗" {
  run grep -rn '✗' "$WEB/public" "$WEB/lib"
  [ "$status" -eq 1 ]
}

@test "no :hover rule changes a border colour" {
  run grep -rnE ':hover[^{]*\{[^}]*border(-[a-z]+)?-color' "$WEB/public"
  [ "$status" -eq 1 ]
}

@test "no literal hex colour in the page or its view model; colours come from /gen/theme.css" {
  run grep -rnE '#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?\b' "$WEB/public" --include='*.css' --include='*.html'
  [ "$status" -eq 1 ]
  run grep -rnE "['\"\`]#[0-9a-fA-F]{3,8}['\"\`]" "$WEB/public" "$WEB/lib" --include='*.js'
  [ "$status" -eq 1 ]
}

@test "loading indicators are static: no animation or transition on .load-progress or .skel" {
  run grep -nE '\.(load-progress|skel)' "$WEB/public/style.css"
  [ "$status" -eq 0 ]
  run grep -E '\b(animation|transition)\b' <<<"$output"
  [ "$status" -eq 1 ]
}

@test ".status.wide sets gap var(--sp-sm)" {
  run grep -nE '\.status\.wide[^{]*\{[^}]*gap:\s*var\(--sp-sm\)' "$WEB/public/style.css"
  [ "$status" -eq 0 ]
}

@test "PR state is read from prPresentation, never re-derived" {
  run grep -rnwE 'prIssues|ciMark|cardTags|cardBadges|cardNumber|decisionText|postKind|healthKind|fileMark' "$WEB/public" "$WEB/lib"
  [ "$status" -eq 1 ]
}

@test "status glyphs and Batch working never live in the web views" {
  run grep -rnE 'GLYPH *=|glyph:|Batch working' "$WEB/public" "$WEB/lib"
  [ "$status" -eq 1 ]
}

@test "css.js does not hard-code --status-* values" {
  run grep -nE 'status-(pass|fail|running|warn|none):var' "$WEB/lib/css.js"
  [ "$status" -eq 1 ]
}

@test "web card and detail header draw every presentation slot" {
  for s in ref title tags author branch stats badges; do
    grep -qE "\\bp\\.$s\\b" "$WEB/public/board_view.js"
  done
  for s in ref title tags author branch base_branch stats detail_issues ci decision review busy; do
    grep -qE "\\bp\\.$s\\b" "$WEB/public/detail_view.js"
  done
}
