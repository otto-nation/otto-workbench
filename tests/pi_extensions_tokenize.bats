#!/usr/bin/env bats
# Tests for the Pi guards' shared tokenizer, the sleep-guard and the background-guard.
setup() {
  load 'test_helper'
  common_setup

  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_SYNC=true
  mkdir -p "$HOME"

  # Most of the cases below evaluate a guard predicate by spawning `node` to
  # import one .ts module, and nearly all of the ~82ms that costs is startup
  # and type-stripping rather than the predicate. Node's compile cache makes
  # that work survive across processes, taking it to ~59ms.
  #
  # $BATS_FILE_TMPDIR, not $TMPDIR: common_setup pins the latter per test, so a
  # cache written there is discarded before the next case can read it, and the
  # first-run cost would be paid every time. bats removes the file-level
  # directory when the file finishes, so nothing outlives the run.
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# ─── _shared/tokenize ───────────────────────────────────────────────────────
# The scan every guard rule reads. Imports nothing, so node loads it directly.
#
# The cases here are the two defect classes that motivated it, stated as
# properties rather than as the commands that exposed them: a quoted operator
# is content, and a dequoted word is the command it spells.

# _tok COMMAND — prints one `flags:value` per token, space separated.
# Flags: O operator, Q quoted, U unparsed, - plain.
_tok() {
  run node --input-type=module -e "
    const { tokenize } = await import('$REPO_ROOT/ai/pi/extensions/_shared/tokenize.ts');
    process.stdout.write(tokenize(process.argv[1]).map(t =>
      (t.operator ? 'O' : t.unparsed ? 'U' : t.quoted ? 'Q' : '-') + ':' + t.value
    ).join(' '));
  " -- "$1"
}

@test "tokenize: an operator inside quotes is content, not syntax" {
  # `awk 'length > 80' f` was refused as a write and
  # `bash -c 'rm -rf x; echo done'` was permitted as a read — one mistake in
  # two directions, and both are this property.
  _tok "awk 'length > 80' f.txt"
  [ "$output" = "-:awk Q:length > 80 -:f.txt" ]
  _tok "bash -c 'rm -rf x; echo done'"
  [ "$output" = "-:bash -:-c Q:rm -rf x; echo done" ]
  _tok "grep -rn 'a->b' src/"
  [ "$output" = "-:grep -:-rn Q:a->b -:src/" ]
}

@test "tokenize: an unquoted operator is syntax" {
  _tok 'echo hi > /tmp/x'
  [ "$output" = "-:echo -:hi O:> -:/tmp/x" ]
  _tok 'cat f | sed -n 1p'
  [ "$output" = "-:cat -:f O:| -:sed -:-n -:1p" ]
  # `>|` is one operator, not `>` followed by a pipe that splits the statement.
  _tok 'echo hi >| /tmp/x'
  [ "$output" = "-:echo -:hi O:>| -:/tmp/x" ]
}

@test "tokenize: a quoted or escaped command name dequotes to itself" {
  # `'rm' -rf x` and `\rm -rf x` both run rm, and both read as an unknown
  # command to a rule matching the raw word.
  _tok "'rm' -rf x"
  [ "$output" = "Q:rm -:-rf -:x" ]
  _tok '\rm -rf x'
  [ "$output" = "Q:rm -:-rf -:x" ]
}

@test "tokenize: a backslash escape is live in double quotes, literal in single" {
  _tok 'echo "a\"b"'
  [ "$output" = '-:echo Q:a"b' ]
  _tok "echo 'a\\\"b'"
  [ "$output" = '-:echo Q:a\"b' ]
}

@test "tokenize: an unterminated quote is reported, not guessed at" {
  # The operators inside it must not read as syntax: the command was cut
  # somewhere the scan cannot see, and a verdict from the fragments is a
  # verdict on something the shell would never have run.
  _tok "bash -c 'rm -rf x"
  [ "$output" = "-:bash -:-c U:rm -rf x" ]
}

@test "statements: a separator inside quotes does not split the statement" {
  # The bypass this whole change exists to close. `statements()` split on the
  # `;` inside the payload, and neither fragment parsed as a shell wrapper or
  # as a write — so appending `; true` to any refused command defeated the
  # guard entirely.
  run node --input-type=module -e "
    const { statements } = await import('$REPO_ROOT/ai/pi/extensions/_shared/statements.ts');
    process.stdout.write(JSON.stringify(statements(process.argv[1])));
  " -- "bash -c 'rm -rf x; echo done'"
  [ "$output" = '["bash -c '\''rm -rf x; echo done'\''"]' ]
}

@test "statements: a quoted word equal to a separator is an argument" {
  # `echo ';'` tokenizes to a word whose *value* is `;`. Splitting on the value
  # rather than on the operator flag cuts the statement in two and loses the
  # command — the flag is what distinguishes syntax the shell acts on from a
  # separator character passed along as an argument.
  run node --input-type=module -e "
    const { statements } = await import('$REPO_ROOT/ai/pi/extensions/_shared/statements.ts');
    process.stdout.write(JSON.stringify(statements(process.argv[1])));
  " -- "grep ';' f"
  [ "$output" = '["grep '\'';'\'' f"]' ]
}

# passes-at-base: the char-splitter special-cased `2>&1` too, so this held before the rewrite; the case pins that the token-based split did not lose it, and it fails if the redirect-adjacency check or the source-slice reassembly is dropped
@test "statements: a redirect's & is not a statement separator" {
  # `2>&1` scans as four tokens, and cutting at that `&` shatters the statement
  # it sits inside. Reassembled from the source line, not by joining tokens
  # with spaces — a rejoin returns `2 > & 1`, which no rule written against
  # real shell text matches.
  run node --input-type=module -e "
    const { statements } = await import('$REPO_ROOT/ai/pi/extensions/_shared/statements.ts');
    process.stdout.write(JSON.stringify(statements(process.argv[1])));
  " -- 'pytest > /tmp/o.txt 2>&1'
  [ "$output" = '["pytest > /tmp/o.txt 2>&1"]' ]
}

@test "tokenize: a file descriptor stays a word of its own" {
  # 2>&1 is `2`, `>`, `&`, `1`. Callers rely on this shape to tell a redirect
  # with no destination from one that names a file.
  _tok 'pytest > /tmp/o.txt 2>&1'
  [ "$output" = "-:pytest O:> -:/tmp/o.txt -:2 O:> O:& -:1" ]
}

# ─── sleep-guard ──────────────────────────────────────────────────────────
# The half of the no-sleep rule that runs under Pi. Its predicate is in
# detect.ts, which imports nothing, so node can load it directly — index.ts
# imports the Pi SDK as a value and only resolves inside a session.

# _detects COMMAND — prints true or false for isWaitingSleep(COMMAND).
_detects() {
  run node --input-type=module -e "
    const { isWaitingSleep } = await import('$REPO_ROOT/ai/pi/extensions/sleep-guard/detect.ts');
    process.stdout.write(String(isWaitingSleep(process.argv[1])));
  " -- "$1"
}

@test "sleep-guard: a long sleep waiting on a job is a finding" {
  _detects 'sleep 295; echo done'
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "sleep-guard: a short settle before a probe is not" {
  _detects 'sleep 2; curl -sI localhost:8931'
  [ "$output" = false ]
}

@test "sleep-guard: a --sleep flag on another command is not" {
  _detects 'pr ci --wait --sleep 30'
  [ "$output" = false ]
}

@test "sleep-guard: a long sleep on a later line is a finding" {
  _detects 'gh pr checks 1257
sleep 300
gh pr checks 1257'
  [ "$output" = true ]
}

@test "sleep-guard: a long sleep behind a short one is a finding" {
  # Testing only the first match let a one-token `sleep 2 &&` in front wave the
  # real wait through. Both guards read the longest sleep, not the first.
  _detects 'curl -sI localhost:8931; sleep 2 && sleep 300'
  [ "$output" = true ]
}

@test "sleep-guard: a sleep inside a heredoc body is not a finding" {
  # Content being written to a file, not a command being run. Claude's guard
  # exempts these lines, so this one must too — two guards enforcing one rule
  # that disagree about a command are worse than either alone.
  _detects 'cat > /tmp/x/poll.sh <<EOF
sleep 300
EOF'
  [ "$output" = false ]
}

@test "sleep-guard: an indented terminator closes only a <<- heredoc" {
  # Accepting indentation for a plain << would end the body at a line that
  # happens to be the marker word and scan the rest of it as commands.
  _detects 'cat > /tmp/x/poll.sh <<-EOF
sleep 300
  EOF
curl -sI localhost:8931'
  [ "$output" = false ]
}

@test "sleep-guard: a zero-padded duration is read as base ten" {
  # Claude's guard needs a 10# prefix here or the arithmetic aborts on `sleep 08`.
  # JS parses base ten already; the case is asserted so the two stay comparable.
  _detects 'sleep 08'
  [ "$output" = false ]
  _detects 'sleep 060'
  [ "$output" = true ]
}

# ─── background-guard ─────────────────────────────────────────────────────
# The half of the no-detached-backgrounding rule that runs under Pi. Same split
# as sleep-guard above: the predicate is in detect.ts, which imports nothing.

# _backgrounds COMMAND — prints true or false for isDetachedBackground(COMMAND).
_backgrounds() {
  run node --input-type=module -e "
    const { isDetachedBackground } = await import('$REPO_ROOT/ai/pi/extensions/background-guard/detect.ts');
    process.stdout.write(String(isDetachedBackground(process.argv[1])));
  " -- "$1"
}

@test "background-guard: a detached run with output redirected to the repo is a finding" {
  # The shape this guard exists for: a long job detached, its output parked in
  # the worktree, and nothing to report completion.
  _backgrounds 'nohup pr rebase --fix > ignore/rebase.json 2> ignore/rebase.err < /dev/null & echo "pid=$!"'
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "background-guard: a trailing & is a finding" {
  _backgrounds 'npm run dev &'
  [ "$output" = true ]
}

@test "background-guard: a conjunction is not backgrounding" {
  _backgrounds 'git fetch && git rebase origin/main'
  [ "$output" = false ]
}

@test "background-guard: redirects and pipes are not backgrounding" {
  # 2>&1, &>, and |& all contain an ampersand and none of them detach anything.
  _backgrounds 'make build 2>&1 | tee /tmp/out.log'
  [ "$output" = false ]
  _backgrounds 'make build &> /tmp/out.log'
  [ "$output" = false ]
  _backgrounds 'make build |& tee /tmp/out.log'
  [ "$output" = false ]
}

@test "background-guard: a case fallthrough is not backgrounding" {
  _backgrounds 'case "$x" in a) run_a ;;& b) run_b ;; esac'
  [ "$output" = false ]
}

@test "background-guard: an ampersand inside quotes is a literal" {
  _backgrounds "grep 'foo & bar' file.txt"
  [ "$output" = false ]
}

@test "background-guard: nohup as an argument is not an invocation" {
  _backgrounds 'grep -rn nohup ai/guidelines/rules'
  [ "$output" = false ]
}

@test "background-guard: backgrounding inside a heredoc body is not a finding" {
  # Content being written to a file, not a command being run — the same
  # exemption sleep-guard and Claude's hook make.
  _backgrounds 'cat > /tmp/x/serve.sh <<EOF
npm run dev &
EOF'
  [ "$output" = false ]
}

@test "background-guard: an indented terminator closes only a <<- heredoc" {
  _backgrounds 'cat > /tmp/x/serve.sh <<-EOF
npm run dev &
  EOF
curl -sI localhost:3000'
  [ "$output" = false ]
}

@test "background-guard: an unquoted query string is a finding, as it is in bash" {
  # Looks like a false positive and is not: unquoted, `curl http://x?a=1&b=2` is
  # a backgrounded curl followed by the assignment `b=2`. Claude's hook blocks it
  # too. The fix is to quote the URL, so do not "correct" this to false.
  _backgrounds 'curl http://x?a=1&b=2'
  [ "$output" = true ]
  _backgrounds 'curl "http://x?a=1&b=2"'
  [ "$output" = false ]
}

@test "background-guard: the two harnesses agree on the operators" {
  # Claude's hook and this extension enforce the same rule for different
  # harnesses. The regexes are written in two languages, so they cannot be
  # compared textually — these are the cases where a divergence would show.
  # Matched with -F: the patterns are regexes themselves, and BSD and GNU grep
  # disagree on whether a mid-pattern $ is an anchor, so an escaped form that
  # matches locally reads as an anchor under GNU grep and matches nothing.
  local guard="$REPO_ROOT/ai/claude/bin/claude-bash-guard"
  grep -qF "re_background='(^|[^&>|;])&([^&>]|\$)'" "$guard"
  grep -qF "re_nohup='(^|[;&|][[:space:]]*)nohup[[:space:]]'" "$guard"
}

@test "sleep-guard: the two harnesses share one threshold" {
  # Claude's hook and this extension enforce the same rule for different
  # harnesses. Two constants that drift apart are one rule with two meanings, and
  # nothing else in either tree would report it.
  local pi_value claude_value
  pi_value=$(grep -oE 'THRESHOLD_SECONDS = [0-9]+' \
    "$REPO_ROOT/ai/pi/extensions/sleep-guard/detect.ts" | grep -oE '[0-9]+')
  claude_value=$(grep -oE '^SLEEP_WAIT_THRESHOLD_SECONDS=[0-9]+' \
    "$REPO_ROOT/ai/claude/bin/claude-bash-guard" | grep -oE '[0-9]+')
  [ -n "$pi_value" ]
  [ -n "$claude_value" ]
  [ "$pi_value" = "$claude_value" ]
}
