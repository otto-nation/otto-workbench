#!/usr/bin/env bats
# Tests for the Pi bash-timeout-guard predicate.
setup() {
  load 'test_helper'
  common_setup
  # Shared across the file's cases so node's type-stripping is paid once; see
  # pi_extensions_testpipe.bats for why $BATS_FILE_TMPDIR rather than $TMPDIR.
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# _finding COMMAND [TIMEOUT] — prints `problem writer` for timeoutFinding, or `null`.
_finding() {
  run node --input-type=module -e "
    const { timeoutFinding } = await import('$REPO_ROOT/ai/pi/extensions/bash-timeout-guard/detect.ts');
    const t = process.argv[2] === '' ? undefined : Number(process.argv[2]);
    const f = timeoutFinding(process.argv[1], t);
    process.stdout.write(f === null ? 'null' : (f.problem + ' ' + f.writer).trim());
  " -- "$1" "${2:-}"
}

@test "bash-timeout-guard: no timeout is never a finding" {
  _finding 'git push origin feat'
  [ "$status" -eq 0 ]
  [ "$output" = null ]
}

@test "bash-timeout-guard: a millisecond value passed as seconds is too long" {
  _finding 'echo hi' 15000
  [ "$status" -eq 0 ]
  [ "$output" = too_long ]
}

@test "bash-timeout-guard: an hour is the longest bound accepted" {
  _finding 'echo hi' 3600
  [ "$output" = null ]
  _finding 'echo hi' 3601
  [ "$output" = too_long ]
}

@test "bash-timeout-guard: a short bound on git is refused" {
  _finding 'git status' 15
  [ "$output" = "short_on_writer git" ]
}

@test "bash-timeout-guard: git anywhere in a statement list counts" {
  _finding 'cd /r && git -C /r fetch -q origin' 30
  [ "$output" = "short_on_writer git" ]
}

@test "bash-timeout-guard: an environment prefix does not hide the program" {
  _finding 'GIT_TRACE=1 git fetch' 30
  [ "$output" = "short_on_writer git" ]
}

@test "bash-timeout-guard: suites, pr and repo scripts are writers" {
  _finding 'pytest -q tests/x_test.py > /tmp/o 2>&1' 60
  [ "$output" = "short_on_writer pytest" ]
  _finding 'ai/bin/pr batch status' 20
  [ "$output" = "short_on_writer pr" ]
  _finding '/abs/repo/bin/local/validate-all' 120
  [ "$output" = "short_on_writer /abs/repo/bin/local/validate-all" ]
}

@test "bash-timeout-guard: the writer floor is five minutes" {
  _finding 'git fetch' 299
  [ "$output" = "short_on_writer git" ]
  _finding 'git fetch' 300
  [ "$output" = null ]
}

@test "bash-timeout-guard: a short bound on a command that writes no git state is allowed" {
  _finding 'ls -la /tmp' 10
  [ "$output" = null ]
}

# passes-at-base: negative cases — a word that is an argument or a string is not the program run
@test "bash-timeout-guard: git named as an argument or in quotes is not git run" {
  _finding 'grep -rn git notes.md' 10
  [ "$output" = null ]
  _finding "echo 'git push origin main'" 10
  [ "$output" = null ]
}

@test "bash-timeout-guard: a coreutils timeout shorter than the tool's is the gentle bound" {
  _finding 'timeout 10 git fetch origin' 15
  [ "$output" = null ]
  _finding 'timeout -k 5 10s git fetch origin' 15
  [ "$output" = null ]
}

@test "bash-timeout-guard: a coreutils timeout longer than the tool's is still cut off by it" {
  _finding 'timeout 600 git fetch origin' 30
  [ "$output" = "short_on_writer git" ]
  _finding 'timeout 2m pytest tests/' 60
  [ "$output" = "short_on_writer pytest" ]
}
