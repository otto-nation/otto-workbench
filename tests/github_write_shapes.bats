#!/usr/bin/env bats
# The shared GitHub write-shape corpus, end to end through both harnesses'
# subagent-publishing rules: $REPO_ROOT/ai/claude/bin/claude-bash-guard with a
# non-empty agent_id, and isPublishing in
# $REPO_ROOT/ai/pi/extensions/oneshot-publish-guard/detect.ts.
#
# Each harness keeps only a cheap superset pre-filter and asks
# $REPO_ROOT/ai/bin/owners-check --classify for the verdict, so a write row the
# pre-filter missed shows here as a write that was allowed. The Python
# classifier is held to the same file by tests/gh_write_shapes_test.py.
# select-on: ai/pi/extensions/* ai/claude/bin/* ai/bin/* ai/lib/gh/* tests/fixtures/github-write-shapes.tsv

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  mkdir -p "$HOME"
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  CORPUS="$REPO_ROOT/tests/fixtures/github-write-shapes.tsv"
  SUBAGENT_REFUSAL="does not file issues"
}

teardown() {
  common_teardown
}

# _claude_subagent GUARD COMMAND — publish when GUARD refuses COMMAND from a
# subagent with the publishing message, allowed otherwise. Another rule's
# refusal is not this rule's answer, so it reads as allowed.
_claude_subagent() {
  local payload output
  payload=$(python3 -c '
import json, sys
print(json.dumps({"agent_id": "abc", "tool_input": {"command": sys.argv[1]}}))
' "$2")
  output=$(printf '%s' "$payload" | "$1" 2>&1) || true
  if [[ "$output" == *"$SUBAGENT_REFUSAL"* ]]; then
    echo publish
  else
    echo allowed
  fi
}

# _pi_publishes COMMAND [CHECKER] — isPublishing's answer, true or false.
_pi_publishes() {
  run node --input-type=module -e "
    const { isPublishing } = await import('$REPO_ROOT/ai/pi/extensions/oneshot-publish-guard/detect.ts');
    const answer = process.argv[2] ? isPublishing(process.argv[1], process.argv[2]) : isPublishing(process.argv[1]);
    process.stdout.write(String(answer));
  " -- "$1" "${2:-}"
}

# _guard_copy CHECKER_BODY — a copy of the guard at $TMPDIR/tree/ai/claude/bin,
# with an owners-check at ai/bin holding CHECKER_BODY, or none when it is empty.
# Prints the copy's path.
_guard_copy() {
  mkdir -p "$TMPDIR/tree/ai/claude/bin" "$TMPDIR/tree/ai/bin"
  cp "$REPO_ROOT/ai/claude/bin/claude-bash-guard" "$TMPDIR/tree/ai/claude/bin/"
  if [[ -n "$1" ]]; then
    printf '#!/usr/bin/env bash\n%s\n' "$1" > "$TMPDIR/tree/ai/bin/owners-check"
    chmod +x "$TMPDIR/tree/ai/bin/owners-check"
  fi
  printf '%s' "$TMPDIR/tree/ai/claude/bin/claude-bash-guard"
}

@test "github write shapes: a Claude subagent is refused every write row and no read row" {
  local expected command want mismatches=""
  while IFS=$'\t' read -r expected command || [[ -n "$expected" ]]; do
    [[ -z "$expected" || "$expected" == \#* ]] && continue
    want=allowed
    [[ "$expected" == write ]] && want=publish
    [[ "$(_claude_subagent "$REPO_ROOT/ai/claude/bin/claude-bash-guard" "$command")" == "$want" ]] ||
      mismatches+="  expected $expected: $command"$'\n'
  done < "$CORPUS"
  [ -z "$mismatches" ] || { printf '%s' "$mismatches"; return 1; }
}

@test "github write shapes: a one-shot Pi session is refused every write row and no read row" {
  run node --input-type=module -e "
    const { readFileSync } = await import('node:fs');
    const { isPublishing } = await import('$REPO_ROOT/ai/pi/extensions/oneshot-publish-guard/detect.ts');
    const bad = [];
    for (const line of readFileSync(process.argv[1], 'utf8').split('\n')) {
      if (!line || line.startsWith('#')) continue;
      const tab = line.indexOf('\t');
      const expected = line.slice(0, tab);
      const command = line.slice(tab + 1);
      if ((isPublishing(command) ? 'write' : 'read') !== expected) bad.push('expected ' + expected + ': ' + command);
    }
    process.stdout.write(bad.join('\n'));
  " -- "$CORPUS"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "github write shapes: the Pi pre-filter matches every write row" {
  run node --input-type=module -e "
    const { readFileSync } = await import('node:fs');
    const { mightWriteGithub } = await import('$REPO_ROOT/ai/pi/extensions/_shared/github-writes.ts');
    const missed = [];
    for (const line of readFileSync(process.argv[1], 'utf8').split('\n')) {
      if (!line.startsWith('write\t')) continue;
      const command = line.slice('write\t'.length);
      if (!mightWriteGithub(command)) missed.push(command);
    }
    process.stdout.write(missed.join('\n'));
  " -- "$CORPUS"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "github write shapes: a write inside a heredoc body is content, in both harnesses" {
  local cmd=$'cat > /tmp/x/notes.md <<EOF\ngh issue create --title x\nEOF'
  [ "$(_claude_subagent "$REPO_ROOT/ai/claude/bin/claude-bash-guard" "$cmd")" = allowed ]
  _pi_publishes "$cmd"
  [ "$status" -eq 0 ]
  [ "$output" = false ]
}

@test "github write shapes: a heredoc body with an apostrophe does not hide the write, in both harnesses" {
  local cmd=$'gh pr comment 1 --body-file - <<\'EOF\'\nIt\'s done\nEOF'
  [ "$(_claude_subagent "$REPO_ROOT/ai/claude/bin/claude-bash-guard" "$cmd")" = publish ]
  _pi_publishes "$cmd"
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "github write shapes: Claude counts a pre-filter match as publishing when owners-check is missing" {
  local guard
  guard=$(_guard_copy '')
  rm -f "$TMPDIR/tree/ai/bin/owners-check"
  [ "$(_claude_subagent "$guard" 'gh pr view 1')" = publish ]
}

@test "github write shapes: Claude counts a pre-filter match as publishing when owners-check crashes" {
  local guard
  guard=$(_guard_copy 'exit 3')
  [ "$(_claude_subagent "$guard" 'gh pr view 1')" = publish ]
}

@test "github write shapes: Claude takes only exit 0 with stdout read as not publishing" {
  local guard
  guard=$(_guard_copy 'echo read; exit 0')
  [ "$(_claude_subagent "$guard" 'gh issue create --title x')" = allowed ]
  guard=$(_guard_copy 'printf "  read\n"; exit 0')
  [ "$(_claude_subagent "$guard" 'gh issue create --title x')" = allowed ]
}

@test "github write shapes: Claude counts owners-check exiting 1 with a traceback as publishing" {
  local guard
  guard=$(_guard_copy 'echo "Traceback (most recent call last):" >&2; exit 1')
  [ "$(_claude_subagent "$guard" 'gh pr view 1')" = publish ]
}

@test "github write shapes: Claude counts any answer but a clean read as publishing" {
  local guard body
  for body in 'echo read; exit 1' 'echo write; exit 0' 'exit 0' 'echo reader; exit 0' 'printf "read\nread\n"; exit 0'; do
    guard=$(_guard_copy "$body")
    [ "$(_claude_subagent "$guard" 'gh pr view 1')" = publish ] || { echo "allowed with: $body"; return 1; }
  done
}

@test "github write shapes: Claude never runs owners-check on a command the pre-filter rejects" {
  local guard
  guard=$(_guard_copy "touch '$TMPDIR/checker-ran'; exit 0")
  [ "$(_claude_subagent "$guard" 'git log --oneline -3')" = allowed ]
  [ ! -e "$TMPDIR/checker-ran" ]
  [ "$(_claude_subagent "$guard" 'gh pr view 1')" = publish ]
  [ -e "$TMPDIR/checker-ran" ]
}

@test "github write shapes: Pi counts a pre-filter match as publishing when owners-check is missing" {
  _pi_publishes 'gh pr view 1' "$TMPDIR/no-such-checker"
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "github write shapes: Pi counts a pre-filter match as publishing when owners-check crashes" {
  printf '#!/usr/bin/env bash\nexit 3\n' > "$TMPDIR/crashing-checker"
  chmod +x "$TMPDIR/crashing-checker"
  _pi_publishes 'gh pr view 1' "$TMPDIR/crashing-checker"
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

# _pi_checker NAME BODY — an executable checker stub at $TMPDIR/NAME; prints its path.
_pi_checker() {
  printf '#!/usr/bin/env bash\n%s\n' "$2" > "$TMPDIR/$1"
  chmod +x "$TMPDIR/$1"
  printf '%s' "$TMPDIR/$1"
}

@test "github write shapes: Pi takes only exit 0 with stdout read as not publishing" {
  _pi_publishes 'gh issue create --title x' "$(_pi_checker reading 'echo read; exit 0')"
  [ "$status" -eq 0 ]
  [ "$output" = false ]
  _pi_publishes 'gh issue create --title x' "$(_pi_checker padded 'printf "  read\n"; exit 0')"
  [ "$output" = false ]
}

@test "github write shapes: Pi counts owners-check exiting 1 with a traceback as publishing" {
  _pi_publishes 'gh pr view 1' "$(_pi_checker tb 'echo "Traceback (most recent call last):" >&2; exit 1')"
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "github write shapes: Pi counts any answer but a clean read as publishing" {
  local body n=0
  for body in 'echo read; exit 1' 'echo write; exit 0' 'exit 0' 'echo reader; exit 0' 'printf "read\nread\n"; exit 0'; do
    n=$((n + 1))
    _pi_publishes 'gh pr view 1' "$(_pi_checker "odd$n" "$body")"
    [ "$output" = true ] || { echo "allowed with: $body"; return 1; }
  done
}

@test "github write shapes: Pi never runs owners-check on a command the pre-filter rejects" {
  printf '#!/usr/bin/env bash\ntouch "%s/checker-ran"\nexit 0\n' "$TMPDIR" > "$TMPDIR/marking-checker"
  chmod +x "$TMPDIR/marking-checker"
  _pi_publishes 'git log --oneline -3' "$TMPDIR/marking-checker"
  [ "$output" = false ]
  [ ! -e "$TMPDIR/checker-ran" ]
  _pi_publishes 'gh pr view 1' "$TMPDIR/marking-checker"
  [ "$output" = true ]
  [ -e "$TMPDIR/checker-ran" ]
}
