#!/usr/bin/env bats
# Tests for the Pi test-pipe-guard and exit-status-guard.
setup() {
  load 'test_helper'
  common_setup

  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_SYNC=true
  mkdir -p "$HOME"

  # Around 150 cases below evaluate a guard predicate by spawning `node` to
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

# ── test-pipe-guard ─────────────────────────────────────────────────────────
#
# Same split as sleep-guard and background-guard above: the predicate is in
# detect.ts, which imports nothing, so node can load it directly.

# _pipes COMMAND — prints true or false for isPipedTestRun(COMMAND).
_pipes() {
  run node --input-type=module -e "
    const { isPipedTestRun } = await import('$REPO_ROOT/ai/pi/extensions/test-pipe-guard/detect.ts');
    process.stdout.write(String(isPipedTestRun(process.argv[1])));
  " -- "$1"
}

@test "test-pipe-guard: a suite piped into tail is a finding" {
  _pipes 'pytest tests/ -q | tail -6'
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "test-pipe-guard: the redirect that keeps the status is not" {
  _pipes 'pytest tests/ -q > /tmp/out.txt 2>&1; echo $?'
  [ "$output" = false ]
}

@test "test-pipe-guard: pipefail keeps the suite's own status" {
  _pipes 'set -o pipefail; pytest tests/ | tail -3'
  [ "$output" = false ]
}

@test "test-pipe-guard: a || fallback is not a pipe" {
  # `pytest ... || echo failed` already reports the suite's status. Splitting
  # naively on `|` would read it as a pipe into `| echo`.
  _pipes 'pytest tests/ -q || echo failed'
  [ "$output" = false ]
}

@test "test-pipe-guard: naming a runner as an argument is not invoking one" {
  _pipes 'grep pytest notes.md | head'
  [ "$output" = false ]
}

@test "test-pipe-guard: piping something that is not a suite is fine" {
  _pipes 'git log --oneline | head -5'
  [ "$output" = false ]
}

@test "test-pipe-guard: a runner reached by path still matches" {
  _pipes 'bin/local/run-tests | head -20'
  [ "$output" = true ]
}

@test "test-pipe-guard: an env prefix is not a way around the rule" {
  _pipes 'WORKBENCH_X=1 pytest tests/ | wc -l'
  [ "$output" = true ]
}

@test "test-pipe-guard: a suite inside a heredoc body is content, not a command" {
  _pipes 'cat > /tmp/s.sh <<EOF
pytest tests/ | tail -1
EOF'
  [ "$output" = false ]
}

@test "test-pipe-guard: a runner that starts a later line still matches" {
  _pipes 'echo start
pytest tests/ -q | tail -6'
  [ "$output" = true ]
}

@test "test-pipe-guard: a runner as a non-leading pipeline stage still matches" {
  _pipes 'cat file | pytest tests/ | tail -5'
  [ "$output" = true ]
}

@test "test-pipe-guard: a filter that is not the segment right after the runner still matches" {
  _pipes 'pytest tests/ -q | jq . | tail -5'
  [ "$output" = true ]
}

@test "test-pipe-guard: a build command piped into a filter is fine" {
  _pipes 'npm run build | tail -20'
  [ "$output" = false ]
}

@test "test-pipe-guard: a go build piped into a filter is fine" {
  _pipes 'go build ./... | tail -20'
  [ "$output" = false ]
}

@test "test-pipe-guard: a cargo build piped into a filter is fine" {
  _pipes 'cargo build 2>&1 | tail -50'
  [ "$output" = false ]
}

@test "test-pipe-guard: go test piped into a filter is a finding" {
  _pipes 'go test ./... | tail -20'
  [ "$output" = true ]
}

@test "test-pipe-guard: npm test piped into a filter is a finding" {
  _pipes 'npm test | tail -20'
  [ "$output" = true ]
}

@test "test-pipe-guard: npm run test piped into a filter is a finding" {
  _pipes 'npm run test | tail -5'
  [ "$output" = true ]
}

# The runner-list grep that stood here compared the two TEST_RUNNERS literals as
# text. It was deleted rather than extended: the harness-agreement vectors below
# cover every runner in the list by asking what each implementation *answers*,
# and a list comparison passes while the two engines disagree about a command —
# which is what they did, on 46 of a 4138-command corpus, with byte-identical
# lists on both sides.

# _claude_guard COMMAND — prints "blocked" or "allowed" for the Claude hook.
#
# The guard's own status is read directly rather than through `$?` after an
# assignment: `out=$(...)` succeeds whatever the command inside it did, so a
# `$?` on the next line reports the assignment and this helper would answer
# "allowed" for every command — including the ones it exists to catch.
_claude_guard() {
  local payload
  payload=$(_json_command_payload "$1")
  if printf '%s' "$payload" | "$REPO_ROOT/ai/claude/bin/claude-bash-guard" > /dev/null 2>&1; then
    echo allowed
  else
    echo blocked
  fi
}

# _shared_rule_verdict COMMAND — which shared rule the Claude hook fired on,
# or "allowed".
#
# Classified by the block message rather than by exit status, because the hook
# enforces twelve Claude-only rules alongside the shared ones: `bash -c 'sleep
# 300'` blocks on the sh -c wrapper, not on the sleep, and a status-only
# comparison would read that as the sleep rules disagreeing. A command blocked
# by the wrong rule is a divergence too, and only the message shows it.
_shared_rule_verdict() {
  local payload message
  payload=$(_json_command_payload "$1")
  # The status is discarded on purpose here: what rule fired is the question,
  # and _claude_guard above already covers blocked-vs-allowed on its own status.
  message=$(printf '%s' "$payload" | "$REPO_ROOT/ai/claude/bin/claude-bash-guard" 2>&1 || true)
  case "$message" in
    *"is a wait for something"*) echo sleep ;;
    *"Backgrounding with"*) echo background ;;
    *"Piping a test suite"*) echo test-pipe ;;
    *"Use pr create instead of gh pr create"*) echo pr-create ;;
    *"self-review has open findings"*) echo issue-defer ;;
    *"BLOCKED:"*) echo claude-only ;;
    *) echo allowed ;;
  esac
}

# _pi_shared_verdict RULE COMMAND — the Pi predicate for RULE, as the same
# vocabulary _shared_rule_verdict prints.
_pi_shared_verdict() {
  local rule="$1" predicate module
  case "$rule" in
    sleep) module=sleep-guard; predicate=isWaitingSleep ;;
    background) module=background-guard; predicate=isDetachedBackground ;;
    test-pipe) module=test-pipe-guard; predicate=isPipedTestRun ;;
    pr-create) module=pr-create-guard; predicate=isPrCreate ;;
    # A typo in a vector's rule name would otherwise reach node as an empty
    # module path and fail as an import error, which reads as a guard bug.
    *) echo "unknown rule: $rule"; return 1 ;;
  esac
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/pi/extensions/$module/detect.ts');
    console.log(m.$predicate(process.argv[1]) ? '$rule' : 'allowed');
  " "$2"
  echo "$output"
}

# _agree RULE COMMAND — fail unless both harnesses answer RULE for COMMAND.
#
# A Claude-only rule firing first is accepted as agreement only when Pi also
# declines to fire: the shared rule is then not the one under test.
_agree() {
  local rule="$1" cmd="$2" claude pi
  claude=$(_shared_rule_verdict "$cmd")
  pi=$(_pi_shared_verdict "$rule" "$cmd")
  if [ "$claude" = claude-only ] && [ "$pi" = allowed ]; then
    return 0
  fi
  [ "$claude" = "$pi" ] || {
    echo "harness disagreement on: $cmd"
    echo "  claude=$claude  pi=$pi  (rule under test: $rule)"
    return 1
  }
}

@test "shared rules: every classifier substring still appears in a block message" {
  # _shared_rule_verdict tells the rules apart by a substring of the message the
  # guard prints. Reword a block and that substring stops matching, the verdict
  # falls through to `claude-only`, and `_agree` treats the whole case as "a
  # Claude-only rule fired" — which is the one branch that passes without
  # comparing anything. The corpus test would go green while comparing nothing,
  # so the substrings are pinned here rather than only where they are read.
  # Read from the `block` calls rather than from the file: every one of these
  # phrases also appears in a comment explaining the rule, so a file-wide grep
  # keeps passing after the message itself is reworded.
  local messages phrase phrases
  messages=$(grep -hE '^[[:space:]]*block ' \
    "$REPO_ROOT/ai/claude/bin/claude-bash-guard")
  [ -n "$messages" ]

  # The phrases are read out of _shared_rule_verdict's own `case` rather than
  # restated here. A hand-kept second list is one a sixth rule joins without —
  # which is how `pr-create` reached the classifier with nothing pinning its
  # phrase — and the drift would be invisible, because the untested rule still
  # classifies correctly until someone rewords its message.
  #
  # `claude-only` and the bare `BLOCKED:` fallback are excluded: they are what
  # the classifier answers when no shared phrase matched, so they name no
  # message of their own.
  local case_block arm_count phrase_count
  case_block=$(sed -n '/^_shared_rule_verdict()/,/^}/p' "$BATS_TEST_FILENAME")
  phrases=$(printf '%s\n' "$case_block" \
    | sed -n 's/^[[:space:]]*\*"\(.*\)"\*).*$/\1/p' \
    | grep -v '^BLOCKED:$')
  [ -n "$phrases" ]
  [ "$(printf '%s\n' "$phrases" | wc -l | tr -d ' ')" -ge 5 ]

  # Tie the extraction to the shape of the `case` block itself: every arm ends
  # in `;;`, and exactly two of them (the `BLOCKED:` fallback and the bare
  # `*)` default) name no phrase of their own. If a future arm is reformatted
  # — wrapped onto two lines, single-quoted, indented differently — the sed
  # above stops matching it, `phrases` silently shrinks by one, but the
  # `-ge 5` check above would not notice as long as five still matched. This
  # count comparison catches exactly that: it fails the moment a real arm's
  # phrase goes uncaptured, rather than only when the whole list gets short.
  arm_count=$(printf '%s\n' "$case_block" | grep -cE ';;[[:space:]]*$')
  phrase_count=$(printf '%s\n' "$phrases" | wc -l | tr -d ' ')
  [ "$phrase_count" -eq "$((arm_count - 2))" ] || {
    echo "case block has $arm_count arms but only $phrase_count phrases were" \
      "extracted (expected $((arm_count - 2)), i.e. all arms but the two" \
      "fallbacks) — a reformatted arm's phrase went uncaptured"
    return 1
  }

  while IFS= read -r phrase; do
    [ -n "$phrase" ] || continue
    printf '%s' "$messages" | grep -qF "$phrase" || {
      echo "no block message in claude-bash-guard contains: $phrase"
      echo "_shared_rule_verdict classifies on it, and would silently stop"
      return 1
    }
  done <<< "$phrases"
}

@test "shared rules: the two harnesses answer the same on a quoting corpus" {
  # The vectors that were answered differently before the two engines shared a
  # statement scan. Each is a case where one harness read a quoted character as
  # syntax and the other did not; the regexes and the runner lists were
  # byte-identical on both sides throughout, which is why the literal greps
  # that used to stand in for this test reported agreement.
  local cmd

  # A quoted `|` in the last stage. Splitting the raw text made `grep -q 'a|b'`
  # read as a stage named `b'`, so the pipeline looked like it ended somewhere
  # other than a filter.
  for cmd in \
    "pytest tests/ | grep -q 'a|b'" \
    'pytest tests/ | grep -q "a|b"' \
    "bats tests/x.bats | grep 'a|b'" \
    "npm run test | grep 'x|y'"; do
    _agree test-pipe "$cmd"
  done

  # A quoted command name. Deleting the span left no command; keeping it left
  # the runner visible.
  _agree test-pipe "'pytest' tests/ | tail -1"
  _agree test-pipe 'pytest tests/ | "tail" -1'

  # pipefail inside quotes sets nothing, so it must not exempt the pipe.
  _agree test-pipe "echo 'set -o pipefail'; pytest tests/ | tail -1"
  _agree test-pipe 'echo "set -o pipefail"; pytest tests/ | tail -1'

  # An unpaired apostrophe. Two sed passes re-paired it with a later quote and
  # deleted the real command between them. The scan cannot find the span's end
  # either, so it reports the command as unparsed and both harnesses fall back
  # to scanning the text — a quote nobody can close must not turn an operator
  # after it into content, which would be a guard that silently stopped working.
  _agree sleep "echo it's fine; sleep 300"
  _agree sleep "true && echo it's; sleep 300"
  _agree background "echo it's; npm run dev "'&'
  # The fallback still has to say which separator it cut on. Reporting every
  # piece as unpiped let a suite piped into a filter pass whenever the filter's
  # own pattern held an apostrophe.
  _agree test-pipe "pytest tests/ | grep 'it's'"

  # A quoted command name is still the command: `'foo' sleep 300` runs foo with
  # two arguments, so neither harness reads it as a sleep.
  _agree sleep "'foo' sleep 300"
  _agree sleep "foo; 'x' sleep 300"

  # A redirect's `&` is not a backgrounding `&`, and must not split a statement.
  _agree background 'pytest tests/ > out.txt 2>&1'
  _agree sleep 'pytest tests/ > out.txt 2>&1 && sleep 300'

  # `|&` is a pipe — bash's shorthand for `2>&1 |` — and neither harness listed
  # it, so a suite piped through one read as having no pipe at all. It is also
  # not a backgrounding `&`, which is the other way to get this wrong.
  _agree test-pipe 'pytest tests/ |& tail'
  _agree test-pipe 'go test ./... |& head -20'
  _agree test-pipe 'ls |& tail'
  # Not a backgrounding `&`. Tested on a command no other rule claims, so the
  # answer is this rule's rather than whichever one the hook reaches first.
  _agree background 'ls |& tail'

  # gh pr create, the sixth shared rule. Claude matched the raw substring, so
  # these two were refused by one harness and ignored by the other.
  _agree pr-create 'gh pr create --draft'
  _agree pr-create 'echo gh pr create'
  _agree pr-create "grep 'gh pr create' notes.md"
  _agree pr-create 'cd /tmp && gh pr create'
}

@test "test-pipe-guard: the two harnesses agree on example commands, not just word lists" {
  # A text-equal TEST_RUNNERS list is compatible with the two engines
  # disagreeing on a given command — see the multi-line, non-leading-stage,
  # and multi-stage-pipe shapes below, each of which the two implementations
  # once answered differently for.
  local cmd pi_result claude_result
  local -a commands=(
    'echo start
pytest tests/ -q | tail -6'
    'cat file | pytest tests/ | tail -5'
    'pytest tests/ -q | jq . | tail -5'
    'set -o pipefail; pytest tests/ | tail -3'
    'npm run build | tail -20'
    'go test ./... | tail -20'
    'cat > /tmp/s.sh <<EOF
pytest tests/ | tail -1
EOF'
  )
  for cmd in "${commands[@]}"; do
    _pipes "$cmd"
    if [ "$output" = true ]; then pi_result=blocked; else pi_result=allowed; fi
    claude_result=$(_claude_guard "$cmd")
    [ "$pi_result" = "$claude_result" ] || {
      echo "disagreement on: $cmd"
      echo "pi: $pi_result, claude: $claude_result"
      return 1
    }
  done
}

# ─── exit-status-guard ────────────────────────────────────────────────────
# The trailing-report half of the no-masked-status rule. Same split as the
# other guards: the predicate is in detect.ts, which pulls in only ../_shared.
#
# Both arities are exercised. `reportsToCaller` true is the foreground bash
# call, where a redirect to a file is honest; false is job_start, where the
# harness reads the process's exit code and nothing redeems a masked one.

# _masks COMMAND [REPORTS_TO_CALLER] — prints the masked runner, or null.
_masks() {
  run node --input-type=module -e "
    const { maskedRunner } = await import('$REPO_ROOT/ai/pi/extensions/exit-status-guard/detect.ts');
    const { TEST_RUNNERS } = await import('$REPO_ROOT/ai/pi/extensions/test-pipe-guard/detect.ts');
    const reports = process.argv[2] === 'true';
    process.stdout.write(String(maskedRunner(process.argv[1], TEST_RUNNERS, reports)));
  " -- "$1" "${2:-false}"
}

@test "exit-status-guard: a job ending in echo \$? masks the suite status" {
  # The shape that produced three false "succeeded" notices in one session.
  _masks 'npm test > /tmp/out.txt 2>&1; echo "EXIT=$?"'
  [ "$status" -eq 0 ]
  [ "$output" = npm ]
}

@test "exit-status-guard: a bare echo \$? masks it too" {
  _masks 'pytest tests/ ; echo $?'
  [ "$output" = pytest ]
}

@test "exit-status-guard: a printf reporting the status is the same shape" {
  _masks 'bats tests/x.bats; printf "EXIT=%d\n" $?'
  [ "$output" = bats ]
}

@test "exit-status-guard: a trailing redirect after \$? does not hide the report" {
  # A naive split on every literal `&` breaks `2>&1` apart from the `>` in
  # front of it, shattering the last statement into fragments too short to
  # match REPORTS_STATUS — the redirect must stay attached to its statement.
  _masks 'pytest tests/; echo "EXIT=$?" 2>&1' false
  [ "$output" = pytest ]
  _masks 'bats tests/x.bats; printf "EXIT=%d\n" $? 2>&1' false
  [ "$output" = bats ]
  # The reverse order — `&` before `>` — is the same redirect and must stay
  # attached too, or the split shatters the statement the same way.
  _masks 'pytest tests/; echo "EXIT=$?" &>out.txt' false
  [ "$output" = pytest ]
}

@test "exit-status-guard: the runner alone is fine" {
  # The remedy the refusal names: let the runner be the last thing that runs.
  _masks 'npm test > /tmp/out.txt 2>&1'
  [ "$output" = null ]
}

@test "exit-status-guard: a trailing progress line is not a status claim" {
  # `echo done` masks a status too, but reads as progress rather than a report.
  # Blocking it would make this guard noise.
  _masks 'npm test; echo done'
  [ "$output" = null ]
}

@test "exit-status-guard: a non-runner command reporting its status is fine" {
  # The rule is about a test/validator status being discarded, not about every
  # use of `$?`.
  _masks 'curl -sI localhost:8931; echo "EXIT=$?"'
  [ "$output" = null ]
}

@test "exit-status-guard: persisting the status is honest in the foreground only" {
  # `echo $? >> out.txt` keeps the status for something else to read, which is
  # a real pattern in a bash call. Under job_start it still leaves the job's
  # own exit code masked, and that code is what the completion notice reports.
  _masks 'npm test > /tmp/out.txt 2>&1; echo $? >> /tmp/out.txt' true
  [ "$output" = null ]
  _masks 'npm test > /tmp/out.txt 2>&1; echo $? >> /tmp/out.txt' false
  [ "$output" = npm ]
}

@test "exit-status-guard: a report inside a heredoc body is not a finding" {
  # Content written to a file, not commands being run — the same exemption the
  # other guards make, via the shared statement splitter.
  _masks 'cat > /tmp/run.sh <<EOF
npm test
echo "EXIT=$?"
EOF'
  [ "$output" = null ]
}

@test "exit-status-guard: a leading env assignment does not hide the runner" {
  _masks 'CI=1 npm test; echo "EXIT=$?"'
  [ "$output" = npm ]
}

@test "exit-status-guard: a path-qualified runner is named by its basename" {
  _masks 'bin/local/run-tests; echo "EXIT=$?"'
  [ "$output" = run-tests ]
}

@test "exit-status-guard: the runner list is test-pipe-guard's, not a copy" {
  # A second runner list here would drift from the one Claude's hook is held
  # to, and nothing else in the tree would report it. The assertion is about a
  # *declaration*: both files name TEST_RUNNERS in prose, so a bare grep for
  # the word passes whatever the code does.
  run grep -rE '^\s*(export\s+)?const\s+TEST_RUNNERS\s*=' \
    "$REPO_ROOT/ai/pi/extensions/exit-status-guard/"
  [ "$status" -ne 0 ]
  run grep -q 'TEST_RUNNERS } from "../test-pipe-guard/detect.ts"' \
    "$REPO_ROOT/ai/pi/extensions/exit-status-guard/index.ts"
  [ "$status" -eq 0 ]
}

@test "exit-status-guard: job_start is the tool whose status is a process code" {
  # The mapping the guard turns on, and the one thing index.ts cannot be tested
  # for directly — it imports the SDK. Backwards, the job_start case would be
  # exempt whenever the command redirects, which is nearly always, and the
  # false-green shape this guard exists for would sail through.
  run node --input-type=module -e "
    const { readsPrintedStatus } = await import('$REPO_ROOT/ai/pi/extensions/exit-status-guard/detect.ts');
    process.stdout.write([
      readsPrintedStatus('bash'),
      readsPrintedStatus('job_start'),
    ].join(','));
  "
  [ "$status" -eq 0 ]
  [ "$output" = 'true,false' ]
}

@test "exit-status-guard: index.ts passes each tool its own status model" {
  # A literal true/false at either call site would pass the test above while
  # the wiring said the opposite. Both call sites must go through the mapping.
  run grep -q 'readsPrintedStatus("bash")' \
    "$REPO_ROOT/ai/pi/extensions/exit-status-guard/index.ts"
  [ "$status" -eq 0 ]
  run grep -q 'readsPrintedStatus("job_start")' \
    "$REPO_ROOT/ai/pi/extensions/exit-status-guard/index.ts"
  [ "$status" -eq 0 ]
  # A literal at either call site would satisfy both greps above while the
  # wiring said the opposite of the mapping.
  run grep -nE 'maskedRunner\([^)]*,\s*(true|false)\s*\)' \
    "$REPO_ROOT/ai/pi/extensions/exit-status-guard/index.ts"
  [ "$status" -ne 0 ]
}
