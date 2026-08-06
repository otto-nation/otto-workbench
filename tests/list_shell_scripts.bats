#!/usr/bin/env bats
# Tests for list_shell_scripts() in lib/files.sh — the file selection behind
# the pre-push ShellCheck step.

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"

  TMPDIR="$(mktemp -d)"
  ROOT="$TMPDIR/repo"
  mkdir -p "$ROOT"
}

teardown() {
  rm -rf "$TMPDIR"
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
  printf '#!/usr/bin/env bats\ncat > stub << EOF\n#!/bin/bash\nEOF\n' > "$ROOT/suite.bats"

  run list_shell_scripts "$ROOT"

  [ -z "$output" ]
}

@test "list_shell_scripts: keeps real scripts when a heredoc file is present" {
  printf '#!/usr/bin/env bats\ncat << EOF\n#!/bin/bash\nEOF\n' > "$ROOT/suite.bats"
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
