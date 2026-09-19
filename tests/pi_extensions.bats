#!/usr/bin/env bats
# Tests for step_pi_extensions — the Pi extension installer.
#
# The subject is filesystem reconciliation, so this mirrors skills_install.bats
# rather than pi_settings.bats: a fake workbench tree, the real libraries
# sourced against it, and assertions about what survives a prune.

setup() {
  load 'test_helper'
  common_setup

  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_SYNC=true
  mkdir -p "$HOME"

  FAKE_WORKBENCH="$TMPDIR/workbench"
  mkdir -p "$FAKE_WORKBENCH/ai/pi/extensions"
  cp "$REPO_ROOT/ai/pi/steps.sh" "$FAKE_WORKBENCH/ai/pi/steps.sh"

  PI_EXT_DIR="$HOME/.pi/agent/extensions"
}

teardown() {
  common_teardown
}

# _make_extension NAME [ENTRY] — writes a minimal extension into the fake tree.
# ENTRY defaults to index.ts; pass another name to test Pi's other entry points.
_make_extension() {
  local name="$1" entry="${2:-index.ts}"
  local dir="$FAKE_WORKBENCH/ai/pi/extensions/$name"
  mkdir -p "$dir"
  printf 'export default function (pi) {}\n' > "$dir/$entry"
}

# _make_override NAME [ENTRY] — the same, in the operator's override layer.
_make_override() {
  local name="$1" entry="${2:-index.ts}"
  local dir="$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions/$name"
  mkdir -p "$dir"
  printf 'export default function (pi) { /* override */ }\n' > "$dir/$entry"
}

# _make_handwritten NAME — an extension the operator wrote directly into Pi's
# discovery root, which the workbench never installed and must never remove.
_make_handwritten() {
  local dir="$PI_EXT_DIR/$1"
  mkdir -p "$dir"
  printf 'export default function (pi) { /* mine */ }\n' > "$dir/index.ts"
}

# _run_step — sources the real libraries against the fake workbench.
#
# `set -e` matches every production caller, and WORKBENCH_STABLE_DIR is pinned
# to the fake tree because lib/constants.sh only derives it when unset — the
# real checkout's value would otherwise leak in from the environment and
# install_symlink would rewrite every source path back to it.
_run_step() {
  run bash -c "
    set -e
    export WORKBENCH_DIR='$FAKE_WORKBENCH'
    export WORKBENCH_STABLE_DIR='$FAKE_WORKBENCH'
    . '$REPO_ROOT/lib/ui.sh'
    . '$FAKE_WORKBENCH/ai/pi/steps.sh'
    step_pi_extensions
  "
}

# _run_step_from_worktree — the everyday configuration on a worktree machine,
# where WORKBENCH_STABLE_DIR names a different tree from WORKBENCH_DIR.
#
# install_symlink rewrites each source path through the stable dir, so here no
# installed symlink points at WORKBENCH_DIR at all. Pinning the two together
# hides whether ownership is decided against the path actually written.
_run_step_from_worktree() {
  run bash -c "
    set -e
    export WORKBENCH_DIR='$FAKE_WORKBENCH'
    export WORKBENCH_STABLE_DIR='$TMPDIR/main'
    . '$REPO_ROOT/lib/ui.sh'
    . '$FAKE_WORKBENCH/ai/pi/steps.sh'
    step_pi_extensions
  "
}

# ─── Installing ─────────────────────────────────────────────────────────────

@test "an extension is installed into Pi's discovery root" {
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ -L "$PI_EXT_DIR/capture" ]
}

@test "the installed symlink points at the source directory" {
  _make_extension capture
  _run_step
  [ "$(readlink "$PI_EXT_DIR/capture")" = "$FAKE_WORKBENCH/ai/pi/extensions/capture" ]
}

@test "the entry point is reachable through the symlink" {
  _make_extension capture
  _run_step
  [ -f "$PI_EXT_DIR/capture/index.ts" ]
}

@test "an extension with an index.js is installed" {
  _make_extension compiled index.js
  _run_step
  [ -L "$PI_EXT_DIR/compiled" ]
}

@test "an extension declaring its own entry points in package.json is installed" {
  _make_extension packaged package.json
  _run_step
  [ -L "$PI_EXT_DIR/packaged" ]
}

@test "a directory with no entry point is skipped rather than installed" {
  # What the skip itself does: the entry-less directory is not installed, and
  # the step still succeeds rather than treating it as fatal.
  mkdir -p "$FAKE_WORKBENCH/ai/pi/extensions/empty"
  _make_extension real
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$PI_EXT_DIR/empty" ]
  [ -L "$PI_EXT_DIR/real" ]
}

@test "a malformed extension does not stop the others installing" {
  # What the skip does to the *loop*: `continue` rather than `return`, so
  # extensions queued after the bad one are still reached. The test above
  # would pass even if the loop aborted, since it has only one good entry.
  mkdir -p "$FAKE_WORKBENCH/ai/pi/extensions/empty"
  _make_extension one
  _make_extension two
  _run_step
  [ "$status" -eq 0 ]
  [ -L "$PI_EXT_DIR/one" ]
  [ -L "$PI_EXT_DIR/two" ]
}

@test "a second run changes nothing" {
  _make_extension capture
  _run_step
  local before
  before="$(readlink "$PI_EXT_DIR/capture")"
  _run_step
  [ "$status" -eq 0 ]
  [ "$(readlink "$PI_EXT_DIR/capture")" = "$before" ]
}

@test "an absent source tree is skipped, not an error" {
  rm -rf "$FAKE_WORKBENCH/ai/pi/extensions"
  _run_step
  [ "$status" -eq 0 ]
}

# ─── Overrides ──────────────────────────────────────────────────────────────

@test "an override replaces the shipped extension of the same name" {
  _make_extension capture
  _make_override capture
  _run_step
  [ "$(readlink "$PI_EXT_DIR/capture")" \
    = "$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions/capture" ]
}

@test "an override adds an extension the workbench does not ship" {
  _make_override mine
  _run_step
  [ -L "$PI_EXT_DIR/mine" ]
}

@test "a .disabled sentinel suppresses a shipped extension" {
  _make_extension capture
  mkdir -p "$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions"
  touch "$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions/capture.disabled"
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$PI_EXT_DIR/capture" ]
}

@test "the sentinel is spelled without the entry point's extension" {
  # resolve_layers keys a "*/" glob on the directory name, which is what makes
  # <name>.disabled work here as it does for skills. A flat <name>.ts layout
  # would key on "capture.ts" and this sentinel would silently do nothing.
  _make_extension capture
  mkdir -p "$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions"
  touch "$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions/capture.ts.disabled"
  _run_step
  [ -L "$PI_EXT_DIR/capture" ]
}

# ─── Pruning ────────────────────────────────────────────────────────────────

@test "an extension removed from the tree is pruned" {
  _make_extension capture
  _run_step
  rm -rf "$FAKE_WORKBENCH/ai/pi/extensions/capture"
  _make_extension other
  _run_step
  [ "$status" -eq 0 ]
  [ ! -L "$PI_EXT_DIR/capture" ]
}

@test "pruning a retired extension removes the dangling symlink itself" {
  # The prune loop globs "$target"/* rather than "$target"/*/ for this: the
  # trailing-slash form only matches entries that resolve as directories, so a
  # dangling link would never be visited and -e alone would pass while the
  # broken link stayed.
  _make_extension capture
  _run_step
  rm -rf "$FAKE_WORKBENCH/ai/pi/extensions/capture"
  _make_extension other
  _run_step
  [ ! -L "$PI_EXT_DIR/capture" ]
  [ ! -e "$PI_EXT_DIR/capture" ]
}

@test "an extension installed from a worktree is pruned once its source is gone" {
  mkdir -p "$TMPDIR/main"
  _make_extension capture
  _run_step_from_worktree
  [ -L "$PI_EXT_DIR/capture" ]
  rm -rf "$FAKE_WORKBENCH/ai/pi/extensions/capture"
  _make_extension other
  _run_step_from_worktree
  [ ! -L "$PI_EXT_DIR/capture" ]
}

# ─── Ownership ──────────────────────────────────────────────────────────────

@test "a hand-written extension directory survives a prune" {
  # ~/.pi/agent/extensions is where Pi's own docs tell an operator to put one,
  # so a real directory here is always theirs.
  _make_handwritten mine
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ -f "$PI_EXT_DIR/mine/index.ts" ]
}

@test "a hand-written extension is reported rather than removed silently" {
  _make_handwritten mine
  _make_extension capture
  _run_step
  [[ "$output" == *"was not installed by the workbench"* ]]
}

@test "a symlink pointing outside the workbench survives a prune" {
  mkdir -p "$TMPDIR/elsewhere/extensions/theirs"
  mkdir -p "$PI_EXT_DIR"
  ln -s "$TMPDIR/elsewhere/extensions/theirs" "$PI_EXT_DIR/theirs"
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ -L "$PI_EXT_DIR/theirs" ]
}

@test "a symlink into the checkout but outside an extensions dir survives" {
  # The extensions/ path segment is what stops ownership swallowing the whole
  # checkout: a hand-placed link to a note or a doc is not something this step
  # ever wrote.
  mkdir -p "$FAKE_WORKBENCH/docs"
  printf 'notes\n' > "$FAKE_WORKBENCH/docs/notes.md"
  mkdir -p "$PI_EXT_DIR"
  ln -s "$FAKE_WORKBENCH/docs/notes.md" "$PI_EXT_DIR/notes.md"
  _make_extension capture
  _run_step
  [ -L "$PI_EXT_DIR/notes.md" ]
}

@test "a plain file in the discovery root survives a prune" {
  mkdir -p "$PI_EXT_DIR"
  printf 'export default function (pi) {}\n' > "$PI_EXT_DIR/theirs.ts"
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ -f "$PI_EXT_DIR/theirs.ts" ]
}

# ─── What is deliberately not installed ─────────────────────────────────────

@test "the real tree's extensions-cli is not installed globally" {
  # review-guard is passed with --extension for review runs only. Installed
  # into the discovery root it would load in every interactive session, where
  # REVIEW_WORKTREE_DIR is unset.
  [ -f "$REPO_ROOT/ai/pi/extensions-cli/review-guard.ts" ]
  [ ! -e "$REPO_ROOT/ai/pi/extensions/review-guard.ts" ]
  [ ! -d "$REPO_ROOT/ai/pi/extensions/review-guard" ]
}

@test "the README beside the extensions is not installed as one" {
  printf '# notes\n' > "$FAKE_WORKBENCH/ai/pi/extensions/README.md"
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$PI_EXT_DIR/README.md" ]
}

@test "every extension in the real tree has an entry point Pi can load" {
  for dir in "$REPO_ROOT"/ai/pi/extensions/*/; do
    [ -d "$dir" ] || continue
    [ -f "${dir}index.ts" ] || [ -f "${dir}index.js" ] || [ -f "${dir}package.json" ]
  done
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

@test "test-pipe-guard: the two harnesses share one runner list" {
  # Claude's hook and this extension enforce the same rule for different
  # harnesses. Two lists that drift apart are one rule with two meanings, and
  # nothing else in either tree would report it.
  local pi_list claude_list
  pi_list=$(sed -n '/^export const TEST_RUNNERS = \[/,/^\];/p' \
    "$REPO_ROOT/ai/pi/extensions/test-pipe-guard/detect.ts" |
    grep -oE '"[a-z-]+"' | tr -d '"' | sort | tr '\n' ' ')
  claude_list=$(grep -oE "^TEST_RUNNERS='[^']+'" \
    "$REPO_ROOT/ai/claude/bin/claude-bash-guard" |
    sed -E "s/^TEST_RUNNERS='([^']+)'/\1/" | tr '|' '\n' | sort | tr '\n' ' ')
  [ -n "$pi_list" ]
  [ -n "$claude_list" ]
  [ "$pi_list" = "$claude_list" ]
}

# _claude_guard COMMAND — prints "blocked" or "allowed" for the Claude hook.
_claude_guard() {
  local out
  out=$(python3 -c '
import json, sys
print(json.dumps({"tool_input": {"command": sys.argv[1]}}))
' "$1" | "$REPO_ROOT/ai/claude/bin/claude-bash-guard" 2>&1)
  if [ "$?" -eq 2 ]; then echo blocked; else echo allowed; fi
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
