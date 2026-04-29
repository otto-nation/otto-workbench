#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  source_lib
  # Small budget keeps chunk sizes manageable in tests
  DIFF_MAX_CHARS=500
}

teardown() {
  common_teardown
}

# _make_chunk PATH BODY_SIZE — builds a minimal but realistic diff chunk.
# Header: "diff --git a/PATH b/PATH\n"  (~len(path)*2 + 15 + 1 chars)
# Body:   BODY_SIZE 'x' characters + newline
# Total size is deterministic given PATH and BODY_SIZE.
#
# IMPORTANT: always capture multiple calls inside a single $() subshell:
#   diff="$( _make_chunk a 50; _make_chunk b 600 )"
# Separate $() calls strip trailing newlines, merging the last body line and
# the next header onto one line, which breaks the "diff --git" split logic.
_make_chunk() {
  local path="$1" body_size="${2:-50}"
  printf 'diff --git a/%s b/%s\n%s\n' "$path" "$path" "$(printf '%*s' "$body_size" '' | tr ' ' 'x')"
}

# ── All chunks fit ────────────────────────────────────────────────────────────

@test "returns all chunks when total diff is within budget" {
  local diff
  diff="$( _make_chunk "a.txt" 50; _make_chunk "b.txt" 50 )"
  run _compact_diff "$diff"
  [ "$status" -eq 0 ]
  [[ "$output" == *"diff --git a/a.txt"* ]]
  [[ "$output" == *"diff --git a/b.txt"* ]]
  [[ "$output" != *"omitted"* ]]
}

# ── Omission ──────────────────────────────────────────────────────────────────

@test "omits a chunk that would exceed the budget" {
  # small chunk ~86 chars; large chunk ~633 chars; budget=500
  # small fits alone; large does not
  local diff
  diff="$( _make_chunk "small.txt" 50; _make_chunk "big.txt" 600 )"
  run _compact_diff "$diff"
  [ "$status" -eq 0 ]
  [[ "$output" == *"diff --git a/small.txt"* ]]
  [[ "$output" != *"diff --git a/big.txt"* ]]
  [[ "$output" == *"omitted"* ]]
}

@test "includes the omitted filename in the trailing note" {
  local diff
  diff="$( _make_chunk "kept.txt" 50; _make_chunk "dropped.txt" 600 )"
  run _compact_diff "$diff"
  [[ "$output" == *"dropped.txt"* ]]
  [[ "$output" == *"1 file(s) omitted"* ]]
}

@test "lists all omitted filenames when multiple files are omitted" {
  local diff
  diff="$( _make_chunk "small.txt" 50; _make_chunk "alpha.txt" 600; _make_chunk "beta.txt" 600 )"
  run _compact_diff "$diff"
  [[ "$output" == *"alpha.txt"* ]]
  [[ "$output" == *"beta.txt"* ]]
  [[ "$output" == *"2 file(s) omitted"* ]]
}

@test "returns only the omitted note when the single file exceeds the budget" {
  local diff
  diff="$( _make_chunk "huge.txt" 600 )"
  run _compact_diff "$diff"
  [ "$status" -eq 0 ]
  [[ "$output" != *"diff --git"* ]]
  [[ "$output" == *"1 file(s) omitted"* ]]
  [[ "$output" == *"huge.txt"* ]]
}

# ── Greedy selection ──────────────────────────────────────────────────────────

@test "prefers smaller chunks to maximise files included within budget" {
  # layout: [large ~276, huge ~634, small ~86]; budget=500
  # greedy by size: small(86) + large(276) = 362 fits; huge(634) does not
  local diff
  diff="$( _make_chunk "large.txt" 240; _make_chunk "huge.txt" 600; _make_chunk "small.txt" 50 )"
  run _compact_diff "$diff"
  [ "$status" -eq 0 ]
  [[ "$output" == *"diff --git a/large.txt"* ]]
  [[ "$output" == *"diff --git a/small.txt"* ]]
  [[ "$output" != *"diff --git a/huge.txt"* ]]
}

# ── Output ordering ───────────────────────────────────────────────────────────

@test "reconstructs included chunks in original diff order, not size order" {
  # layout: [a.txt small, b.txt huge (omitted), c.txt small]
  # both a.txt and c.txt fit; b.txt is omitted
  # output must have a.txt before c.txt (original indices 0, 2)
  local diff
  diff="$( _make_chunk "a.txt" 50; _make_chunk "b.txt" 600; _make_chunk "c.txt" 50 )"
  run _compact_diff "$diff"
  [ "$status" -eq 0 ]
  [[ "$output" == *"diff --git a/a.txt"* ]]
  [[ "$output" == *"diff --git a/c.txt"* ]]
  # a.txt must appear before c.txt in the output
  local a_line c_line
  a_line=$(echo "$output" | grep -n "diff --git a/a.txt" | cut -d: -f1)
  c_line=$(echo "$output" | grep -n "diff --git a/c.txt" | cut -d: -f1)
  (( a_line < c_line ))
}
