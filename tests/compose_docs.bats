#!/usr/bin/env bats
# Tests for bin/local/compose-docs.
#
# A directive is the one place a markdown file gets to run something, so the
# allowlist and the argv-not-shell rule are the load-bearing behavior here —
# most of this suite is about what a source file must not be able to reach.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  ROOT="$TMPDIR/root"
  mkdir -p "$ROOT/bin/local" "$ROOT/git/bin" "$ROOT/docs" "$ROOT/outside"
  ln -s "$REPO_ROOT/lib" "$ROOT/lib"
  COMPOSE="$REPO_ROOT/bin/local/compose-docs"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _stub REL BODY — an executable generator at REL under the fake root.
_stub() {
  local rel="$1" body="$2"
  mkdir -p "$(dirname "$ROOT/$rel")"
  printf '#!/usr/bin/env bash\n%s\n' "$body" > "$ROOT/$rel"
  chmod +x "$ROOT/$rel"
}

# _src NAME CONTENT — a docs/NAME.src.md under the fake root.
_src() {
  printf '%s\n' "$2" > "$ROOT/docs/$1.src.md"
}

# _compose ARGS... — runs compose-docs against the fake root.
_compose() {
  REPO_ROOT="$ROOT" DOCS_DIR="$ROOT/docs" "$COMPOSE" "$@"
}

# ── Expansion ────────────────────────────────────────────────────────────────

@test "expands a directive into the named command's output" {
  _stub bin/gen 'printf "generated line\n"'
  _src page 'before
<!-- include: bin/gen -->
after'

  run _compose --quiet
  [ "$status" -eq 0 ]

  run cat "$ROOT/docs/page.md"
  [[ "$output" == *"before"* ]]
  [[ "$output" == *"generated line"* ]]
  [[ "$output" == *"after"* ]]
  [[ "$output" != *"include:"* ]]
}

@test "leaves a comment that is not an include directive untouched" {
  _src page '<!-- a normal comment -->
<!-- includes: not a directive -->'

  run _compose --quiet
  [ "$status" -eq 0 ]

  run cat "$ROOT/docs/page.md"
  [[ "$output" == *"<!-- a normal comment -->"* ]]
  [[ "$output" == *"<!-- includes: not a directive -->"* ]]
}

@test "passes directive arguments as argv, not through a shell" {
  _stub bin/argv 'printf "[%s]\n" "$@"'
  _src page '<!-- include: bin/argv --emit a;b $(whoami) -->'

  run _compose --quiet
  [ "$status" -eq 0 ]

  run cat "$ROOT/docs/page.md"
  [[ "$output" == *"[--emit]"* ]]
  [[ "$output" == *"[a;b]"* ]]
  [[ "$output" == *'[$(whoami)]'* ]]
}

@test "does not glob a directive argument against the working directory" {
  _stub bin/argv 'printf "[%s]\n" "$@"'
  _src page '<!-- include: bin/argv *.md -->'

  run _compose --quiet
  [ "$status" -eq 0 ]

  run cat "$ROOT/docs/page.md"
  [[ "$output" == *"[*.md]"* ]]
}

@test "keeps the last line of output that has no trailing newline" {
  _stub bin/gen 'printf "first\nlast with no newline"'
  _src page '<!-- include: bin/gen -->'

  run _compose --quiet
  [ "$status" -eq 0 ]

  run cat "$ROOT/docs/page.md"
  [[ "$output" == *"last with no newline"* ]]
}

@test "aborts and writes nothing when a generator exits non-zero" {
  _stub bin/gen 'printf "before\n"; exit 3'
  _src page '<!-- include: bin/gen -->'

  run _compose --quiet
  [ "$status" -eq 3 ]
  [ ! -f "$ROOT/docs/page.md" ]
}

@test "a generator cannot read the document it is included from" {
  _stub bin/greedy 'cat; printf "swallowed\n"'
  _src page 'first
<!-- include: bin/greedy -->
last'

  run _compose --quiet
  [ "$status" -eq 0 ]

  run cat "$ROOT/docs/page.md"
  [[ "$output" == *"last"* ]]
}

# ── Allowlist ────────────────────────────────────────────────────────────────

@test "rejects an absolute path" {
  _stub bin/gen 'printf "x\n"'
  _src page "<!-- include: $ROOT/bin/gen -->"

  run _compose --quiet
  [ "$status" -ne 0 ]
  [[ "$output" == *"repo-relative"* ]]
}

@test "rejects a path containing .." {
  _stub bin/gen 'printf "x\n"'
  _src page '<!-- include: bin/../bin/gen -->'

  run _compose --quiet
  [ "$status" -ne 0 ]
  [[ "$output" == *"repo-relative"* ]]
}

@test "rejects a command outside bin/ and git/bin/" {
  _stub outside/gen 'printf "x\n"'
  _src page '<!-- include: outside/gen -->'

  run _compose --quiet
  [ "$status" -ne 0 ]
  [[ "$output" == *"outside bin/ and git/bin/"* ]]
}

@test "accepts a command under git/bin/" {
  _stub git/bin/gen 'printf "from git bin\n"'
  _src page '<!-- include: git/bin/gen -->'

  run _compose --quiet
  [ "$status" -eq 0 ]

  run cat "$ROOT/docs/page.md"
  [[ "$output" == *"from git bin"* ]]
}

@test "rejects a path that is not executable" {
  mkdir -p "$ROOT/bin"
  printf 'not a program\n' > "$ROOT/bin/data.txt"
  _src page '<!-- include: bin/data.txt -->'

  run _compose --quiet
  [ "$status" -ne 0 ]
  [[ "$output" == *"not an executable file"* ]]
}

# ── Recursion ────────────────────────────────────────────────────────────────

@test "expands a directive found in a generator's own output" {
  _stub bin/outer 'printf "outer\n<!-- include: bin/inner -->\n"'
  _stub bin/inner 'printf "inner\n"'
  _src page '<!-- include: bin/outer -->'

  run _compose --quiet
  [ "$status" -eq 0 ]

  run cat "$ROOT/docs/page.md"
  [[ "$output" == *"outer"* ]]
  [[ "$output" == *"inner"* ]]
  [[ "$output" != *"include:"* ]]
}

@test "fails with a cycle message instead of hanging on a self-include" {
  _stub bin/loop 'printf "<!-- include: bin/loop -->\n"'
  _src page '<!-- include: bin/loop -->'

  run _compose --quiet
  [ "$status" -ne 0 ]
  [[ "$output" == *"check for a cycle"* ]]
}

# ── Banner ───────────────────────────────────────────────────────────────────

@test "places the banner below frontmatter so the block still parses" {
  _src page '---
title: Page
---

body'

  run _compose --quiet
  [ "$status" -eq 0 ]

  run head -n 4 "$ROOT/docs/page.md"
  [ "${lines[0]}" = "---" ]
  [ "${lines[1]}" = "title: Page" ]
  [ "${lines[2]}" = "---" ]
  [[ "${lines[3]}" == "<!-- Generated from docs/page.src.md by bin/local/compose-docs"* ]]
}

@test "places the banner first when there is no frontmatter" {
  _src page 'body'

  run _compose --quiet
  [ "$status" -eq 0 ]

  run head -n 1 "$ROOT/docs/page.md"
  [[ "$output" == "<!-- Generated from docs/page.src.md by bin/local/compose-docs"* ]]
}

# ── Freshness ────────────────────────────────────────────────────────────────

@test "--check passes when the composed file is current" {
  _stub bin/gen 'printf "value\n"'
  _src page '<!-- include: bin/gen -->'
  _compose --quiet

  run _compose --check --quiet
  [ "$status" -eq 0 ]
}

@test "--check fails and names the file when the generator's output changed" {
  _stub bin/gen 'printf "old\n"'
  _src page '<!-- include: bin/gen -->'
  _compose --quiet
  _stub bin/gen 'printf "new\n"'

  run _compose --check --quiet
  [ "$status" -ne 0 ]
  [[ "$output" == *"docs/page.md"* ]]
  [[ "$output" == *"compose-docs"* ]]
}

@test "--check does not rewrite the stale file" {
  _stub bin/gen 'printf "old\n"'
  _src page '<!-- include: bin/gen -->'
  _compose --quiet
  _stub bin/gen 'printf "new\n"'

  run _compose --check --quiet
  [ "$status" -ne 0 ]

  run cat "$ROOT/docs/page.md"
  [[ "$output" == *"old"* ]]
}

@test "--check fails when the composed artifact is missing entirely" {
  _stub bin/gen 'printf "value\n"'
  _src page '<!-- include: bin/gen -->'

  run _compose --check --quiet
  [ "$status" -ne 0 ]
  [[ "$output" == *"docs/page.md"* ]]
}

# ── Listing ──────────────────────────────────────────────────────────────────

@test "--list prints each directive as the argv it would run" {
  _src page 'prose
<!-- include: bin/gen --group core -->
more prose
<!-- include: git/bin/other --emit table -->'

  run _compose --list "$ROOT/docs/page.src.md"
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf 'bin/gen --group core\ngit/bin/other --emit table')" ]
}

@test "--list normalizes the spacing a directive is allowed to have" {
  _src page '  <!--   include:   bin/gen    --group   core   -->'

  run _compose --list "$ROOT/docs/page.src.md"
  [ "$status" -eq 0 ]
  [ "$output" = "bin/gen --group core" ]
}

@test "--list ignores a comment that is not a directive" {
  _src page '<!-- include this in your notes -->'

  run _compose --list "$ROOT/docs/page.src.md"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "--list composes nothing, so an unreachable command is not an error" {
  _src page '<!-- include: /etc/passwd -->'

  run _compose --list "$ROOT/docs/page.src.md"
  [ "$status" -eq 0 ]
  [ "$output" = "/etc/passwd" ]
  [ ! -f "$ROOT/docs/page.md" ]
}

# ── Discovery ────────────────────────────────────────────────────────────────

@test "composes every docs/*.src.md when given no file arguments" {
  _src one 'one'
  _src two 'two'

  run _compose --quiet
  [ "$status" -eq 0 ]
  [ -f "$ROOT/docs/one.md" ]
  [ -f "$ROOT/docs/two.md" ]
}

@test "composes only the named source when one is given" {
  _src one 'one'
  _src two 'two'

  run _compose --quiet "$ROOT/docs/one.src.md"
  [ "$status" -eq 0 ]
  [ -f "$ROOT/docs/one.md" ]
  [ ! -f "$ROOT/docs/two.md" ]
}

@test "fails when the docs directory holds no sources" {
  run _compose --quiet
  [ "$status" -ne 0 ]
  [[ "$output" == *"No .src.md files found"* ]]
}
