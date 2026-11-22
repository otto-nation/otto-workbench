#!/usr/bin/env bats
# Tests for list_shell_scripts() in lib/files.sh — the file selection behind
# the pre-push ShellCheck step.

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"

  ROOT="$TMPDIR/repo"
  mkdir -p "$ROOT"
}

teardown() {
  common_teardown
}

@test "list_shell_scripts: selects a bash shebang on line 1" {
  printf '#!/usr/bin/env bash\necho hi\n' > "$ROOT/script.sh"

  run list_shell_scripts "$ROOT"

  [ "$status" -eq 0 ]
  [[ "$output" == "$ROOT/script.sh" ]]
}

@test "list_shell_scripts: selects extensionless scripts" {
  printf '#!/bin/sh\necho hi\n' > "$ROOT/tool"

  run list_shell_scripts "$ROOT"

  [[ "$output" == "$ROOT/tool" ]]
}

@test "list_shell_scripts: ignores a shebang below line 1" {
  printf '# notes\ncat > stub << EOF\n#!/bin/bash\nEOF\n' > "$ROOT/notes.txt"

  run list_shell_scripts "$ROOT"

  [ -z "$output" ]
}

@test "list_shell_scripts: selects bats suites" {
  printf '#!/usr/bin/env bats\n@test "t" { true; }\n' > "$ROOT/suite.bats"

  run list_shell_scripts "$ROOT"

  [[ "$output" == "$ROOT/suite.bats" ]]
}

@test "list_shell_scripts: keeps real scripts when a heredoc file is present" {
  printf '# notes\ncat << EOF\n#!/bin/bash\nEOF\n' > "$ROOT/notes.txt"
  printf '#!/usr/bin/env bash\necho hi\n' > "$ROOT/script.sh"

  run list_shell_scripts "$ROOT"

  [[ "$output" == "$ROOT/script.sh" ]]
}

@test "list_shell_scripts: skips python files and ignored directories" {
  printf '#!/usr/bin/env bash\n' > "$ROOT/wrapper.py"
  mkdir -p "$ROOT/ignore" "$ROOT/.git"
  printf '#!/usr/bin/env bash\n' > "$ROOT/ignore/scratch.sh"
  printf '#!/usr/bin/env bash\n' > "$ROOT/.git/hook.sh"

  run list_shell_scripts "$ROOT"

  [ -z "$output" ]
}

@test "list_shell_scripts: output is sorted" {
  printf '#!/usr/bin/env bash\n' > "$ROOT/b.sh"
  printf '#!/usr/bin/env bash\n' > "$ROOT/a.sh"

  run list_shell_scripts "$ROOT"

  [[ "${lines[0]}" == "$ROOT/a.sh" ]]
  [[ "${lines[1]}" == "$ROOT/b.sh" ]]
}

@test "list_shell_scripts: ignores node_modules" {
  mkdir -p "$ROOT/node_modules/pkg"
  printf '#!/usr/bin/env bash\necho vendored\n' > "$ROOT/node_modules/pkg/install.sh"
  printf '#!/usr/bin/env bash\necho ours\n' > "$ROOT/ours.sh"

  run list_shell_scripts "$ROOT"

  [ "$status" -eq 0 ]
  [[ "$output" == "$ROOT/ours.sh" ]]
}

@test "lint_shell_scripts: batches shellcheck at 20 files" {
  local i
  for i in $(seq 1 21); do
    printf '#!/usr/bin/env bash\ntrue\n' > "$ROOT/s$i.sh"
  done
  mkdir -p "$TMPDIR/bin"
  # Count path args only: --severity=warning is already on the command line, so
  # a 20-file batch is $# 21 if we counted flags.
  printf '%s\n' '#!/usr/bin/env bash' 'n=0' 'for a in "$@"; do [[ "$a" == -* ]] || n=$((n+1)); done' 'echo "$n" >> "$SC_LOG"' 'exit 0' > "$TMPDIR/bin/shellcheck"
  chmod +x "$TMPDIR/bin/shellcheck"
  SC_LOG="$TMPDIR/sc.log"
  export SC_LOG
  PATH="$TMPDIR/bin:$PATH" run lint_shell_scripts "$ROOT"
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$SC_LOG" | tr -d ' ')" -eq 2 ]
  grep -qx 20 "$SC_LOG"
  grep -qx 1 "$SC_LOG"
}

@test "lint_shell_scripts: a failing batch fails the helper" {
  printf '#!/usr/bin/env bash\ntrue\n' > "$ROOT/ok.sh"
  mkdir -p "$TMPDIR/bin"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 1' > "$TMPDIR/bin/shellcheck"
  chmod +x "$TMPDIR/bin/shellcheck"
  PATH="$TMPDIR/bin:$PATH" run lint_shell_scripts "$ROOT"
  [ "$status" -ne 0 ]
}

@test "pre-push ShellCheck goes through lint_shell_scripts" {
  run grep -nE 'xargs shellcheck' "$REPO_ROOT/git/hooks/pre-push-workbench"
  [ "$status" -ne 0 ]
  run grep -n 'lint_shell_scripts' "$REPO_ROOT/git/hooks/pre-push-workbench"
  [ "$status" -eq 0 ]
}
