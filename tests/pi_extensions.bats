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
    # _-prefixed directories are shared modules imported by the extensions, not
    # extensions themselves. step_pi_extensions skips them outright, which is
    # the behaviour the next tests assert.
    [[ "$(basename "$dir")" == _* ]] && continue
    [ -f "${dir}index.ts" ] || [ -f "${dir}index.js" ] || [ -f "${dir}package.json" ]
  done
}

@test "a shared module is not installed as an extension" {
  # ../_shared is imported by the guards' detect.ts files. Node resolves it
  # from the extension's real path rather than its installed symlink, so it
  # must stay out of ~/.pi/agent/extensions rather than being installed beside
  # them.
  mkdir -p "$FAKE_WORKBENCH/ai/pi/extensions/_shared"
  printf 'export function helper() {}\n' \
    > "$FAKE_WORKBENCH/ai/pi/extensions/_shared/util.ts"
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$PI_EXT_DIR/_shared" ]
  [ -L "$PI_EXT_DIR/capture" ]
}

@test "a shared module is skipped without a warning" {
  # The skip is by name, not by absent entry point, so nothing is reported. A
  # shared module is correct as it stands, and a warning on every sync for a
  # correct directory is what hides the one naming a malformed extension.
  mkdir -p "$FAKE_WORKBENCH/ai/pi/extensions/_shared"
  printf 'export function helper() {}\n' \
    > "$FAKE_WORKBENCH/ai/pi/extensions/_shared/util.ts"
  _make_extension capture
  _run_step
  [[ "$output" != *_shared* ]]
}

@test "a shared module carrying an entry point is still not installed" {
  # Skipped for its name, not for what it holds. Without this, a shared module
  # that grows a package.json for its own dependencies would start loading
  # into every Pi session as an extension.
  mkdir -p "$FAKE_WORKBENCH/ai/pi/extensions/_shared"
  printf 'export default function (pi) {}\n' \
    > "$FAKE_WORKBENCH/ai/pi/extensions/_shared/index.ts"
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$PI_EXT_DIR/_shared" ]
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

# ── issue-defer-guard ────────────────────────────────────────────────────────
# Same split as the guards above: the predicate is in detect.ts, which imports
# nothing. Whether a review has open findings is a filesystem question index.ts
# asks, and the Claude guard's copy of it is covered in claude_settings.bats.

# _files_issue COMMAND — prints true or false for isIssueFiling(COMMAND).
_files_issue() {
  run node --input-type=module -e "
    const { isIssueFiling } = await import('$REPO_ROOT/ai/pi/extensions/issue-defer-guard/detect.ts');
    process.stdout.write(String(isIssueFiling(process.argv[1])));
  " -- "$1"
}

@test "issue-defer-guard: gh issue create is filing" {
  _files_issue 'gh issue create --title x --body-file /tmp/b.md'
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "issue-defer-guard: reads and other subcommands are not filing" {
  local cmd
  for cmd in 'gh issue view 1' 'gh issue list --state open' 'gh issue edit 3 --body x' 'gh pr create'; do
    _files_issue "$cmd"
    [ "$output" = false ] || {
      echo "matched a non-filing command: $cmd"
      return 1
    }
  done
}

@test "issue-defer-guard: the phrase inside another command is not filing" {
  # A guard that fired on any mention would block reading about itself.
  _files_issue 'echo gh issue create'
  [ "$output" = false ]

  _files_issue 'grep -rn "gh issue create" ai/'
  [ "$output" = false ]
}

@test "issue-defer-guard: a filing after a cd still counts" {
  _files_issue 'cd /tmp/repo && gh issue create --title x'
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "issue-defer-guard: a filing on a later line still counts" {
  # A bare `^`/`$` does not cross a literal newline, so a multi-line command —
  # the sanctioned form for a compound cd, per bash-tool.md § Avoid Compound
  # `cd` Commands — must not slip past on a raw whole-string match.
  _files_issue 'cd /tmp/repo
gh issue create --title x'
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "issue-defer-guard: agrees with the Claude guard command for command" {
  # Two guards enforcing one rule that disagree are worse than one guard: which
  # answer you get would depend on which harness you happen to be in. The
  # Claude side needs a repo with an open-findings review before its pattern is
  # reached, so build one (via the same helper claude_settings.bats uses) and
  # compare the pair on every shape.
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"

  local cmd payload claude_blocks pi_blocks
  for cmd in \
    'gh issue create --title x' \
    'cd /tmp && gh issue create' \
    'echo gh issue create' \
    'grep -rn "gh issue create" ai/' \
    'gh issue view 1' \
    'gh issue list'; do

    payload=$(_json_command_payload "$cmd")

    if _guard_in "$sandbox" "$payload" > /dev/null 2>&1; then
      claude_blocks=false
    else
      claude_blocks=true
    fi

    _files_issue "$cmd"
    pi_blocks="$output"

    [ "$claude_blocks" = "$pi_blocks" ] || {
      echo "guards disagree on: $cmd (claude=$claude_blocks pi=$pi_blocks)"
      return 1
    }
  done
}

# _probe SANDBOX — branchReviewHasOpenFindings() as the Pi guard sees it, run
# from SANDBOX/repo with SANDBOX/state as the state root. The probe reads git
# and the filesystem, so unlike the predicate helpers above it needs a cwd.
_probe() {
  run node --input-type=module -e "
    process.chdir(process.argv[1] + '/repo');
    process.env.WORKBENCH_STATE_DIR = process.argv[1] + '/state';
    const { branchReviewHasOpenFindings } = await import('$REPO_ROOT/ai/pi/extensions/issue-defer-guard/detect.ts');
    process.stdout.write(String(branchReviewHasOpenFindings()));
  " -- "$1"
}

@test "issue-defer-guard: the probe sees open findings on the branch" {
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"

  _probe "$sandbox"
  [ "$status" -eq 0 ]
  [ "$output" = true ]
}

@test "issue-defer-guard: the probe is quiet once every finding is ticked" {
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" "x")"

  _probe "$sandbox"
  [ "$output" = false ]
}

@test "issue-defer-guard: the probe fails open with no review, no branch, or no remote" {
  # Each is a state the probe cannot answer in. Blocking on any of them would
  # make the guard fire on repos it knows nothing about, which is worse than
  # the mistake it exists to prevent.
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing")"
  _probe "$sandbox"
  [ "$output" = false ]

  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"
  git -C "$sandbox/repo" checkout -q --detach
  _probe "$sandbox"
  [ "$output" = false ]

  git -C "$sandbox/repo" checkout -q "isaac/fix/thing"
  git -C "$sandbox/repo" remote remove origin
  _probe "$sandbox"
  [ "$output" = false ]
}

@test "issue-defer-guard: both harnesses read the same review for one branch" {
  # The probe and _branch_review_has_open_findings build the review path
  # independently — same repo-from-remote, same branch slug, same state root.
  # A rename on either side makes that guard read an empty directory and fall
  # silent, so the two are held to one answer here.
  local sandbox
  sandbox="$(_review_sandbox "isaac/fix/thing" " ")"

  _probe "$sandbox"
  [ "$output" = true ]

  run _guard_in "$sandbox" '{"tool_input":{"command":"gh issue create --title x"}}'
  [ "$status" -eq 2 ]
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

# ── review-guard ────────────────────────────────────────────────────────────
#
# Same split as the guards above: the predicate is in extensions-cli/detect.ts,
# which imports only ../extensions/_shared/statements.ts, so node loads it
# directly. review-guard.ts itself imports the Pi SDK and cannot be loaded here.

# _blocked COMMAND — prints the refusal for COMMAND, or the empty string.
_blocked() {
  run node --input-type=module -e "
    const { blockedWriteCommand } = await import('$REPO_ROOT/ai/pi/extensions-cli/detect.ts');
    process.stdout.write(blockedWriteCommand(process.argv[1]) ?? '');
  " -- "$1"
}

@test "review-guard: a plain suite run is allowed" {
  _blocked 'pytest tests/test_foo.py'
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "review-guard: the redirect test-pipe-guard prescribes is allowed" {
  # test-pipe-guard refuses a piped suite and names this exact rewrite as the
  # fix. When this was refused too, the two guards left no command the agent
  # could run: it abandoned verification and burned its budget re-reading.
  _blocked 'pytest tests/ > /tmp/out.txt 2>&1'
  [ -z "$output" ]
}

@test "review-guard: a compound cd into a suite run is allowed" {
  _blocked 'cd /repo/wt && pytest tests/ > /tmp/out.txt 2>&1'
  [ -z "$output" ]
}

@test "review-guard: the other scratch roots are allowed too" {
  # The same list claude-bash-guard exempts. The two must agree.
  _blocked 'pytest > /var/folders/ab/cd/T/out.txt 2>&1'
  [ -z "$output" ]
  _blocked 'pytest > /private/tmp/out.txt'
  [ -z "$output" ]
  _blocked 'pytest > /dev/null'
  [ -z "$output" ]
}

@test "review-guard: a write verb in the worktree path is not a write" {
  # \b treats / and - as boundaries, so an unanchored /\brm\b/ matched the
  # branch name and disabled bash for the whole session.
  _blocked 'cd /Users/i/wt/isaac-fix-rm-stale && pytest tests/'
  [ -z "$output" ]
  _blocked 'cd /Users/i/wt/repo-install-hooks && pytest'
  [ -z "$output" ]
  _blocked 'pytest tests/install/test_setup.py'
  [ -z "$output" ]
  _blocked 'grep -rn tee ai/'
  [ -z "$output" ]
}

@test "review-guard: reading git history is allowed" {
  _blocked 'git log --oneline -5'
  [ -z "$output" ]
  _blocked 'git diff HEAD~1'
  [ -z "$output" ]
  # Reaching past global flags must not turn a read into a write.
  _blocked 'git -C /repo log --oneline'
  [ -z "$output" ]
  _blocked 'git -C /repo status'
  [ -z "$output" ]
}

@test "review-guard: a read-only sed is allowed whatever the path contains" {
  # /^\s*sed\s+[^|]*-i/ crossed into the arguments, so `-i` anywhere after the
  # command matched: every read of a path containing `-i` was refused, and the
  # directory under review when this was found was named doc-internal. 18
  # tracked paths in this repo trip it. Same bug as the \b one the WRITE_COMMANDS
  # comment describes; the patterns never got the test that caught it there.
  _blocked "sed -n '55,95p' doc-internal/CLAUDE.md"
  [ -z "$output" ]
  _blocked 'sed -n 1p bin/wt-init'
  [ -z "$output" ]
  _blocked 'sed -n 1p bin/local/validate-tmpdir-isolation'
  [ -z "$output" ]
  _blocked "perl -ne 'print' bin/wt-init"
  [ -z "$output" ]
  # A flag letter inside a quoted expression is data, not a flag.
  _blocked 'sed -n "s/a-i/b/p" f.txt'
  [ -z "$output" ]
  _blocked "cat f | sed -n 1p"
  [ -z "$output" ]
}

@test "review-guard: a read-only curl is allowed when an argument holds -o" {
  _blocked "curl -s https://api.github.com/x -H 'A: -o'"
  [ -z "$output" ]
  _blocked 'curl -sL https://example.com/x'
  [ -z "$output" ]
  _blocked 'wget https://example.com/x-o-y'
  [ -z "$output" ]
}

# passes-at-base: `sh -c` was a complete bypass before this change, so a read-only payload was allowed by the hole rather than by the rule; the case holds the new unwrapping from being written as a blanket refusal of `-c`
@test "review-guard: a read-only payload in a shell wrapper is allowed" {
  # Unwrapped and rescanned, not refused on shape: a payload that only reads is
  # still a read.
  _blocked "bash -c 'pytest tests/'"
  [ -z "$output" ]
  _blocked "sh -c 'grep -rn foo .'"
  [ -z "$output" ]
}

@test "review-guard: a write command at a statement head is refused" {
  _blocked 'rm -rf build'
  [ -n "$output" ]
  _blocked 'cd /repo && rm -rf build'
  [ -n "$output" ]
  _blocked 'cp a b'
  [ -n "$output" ]
  _blocked 'FOO=1 mv a b'
  [ -n "$output" ]
  _blocked '/bin/rm -rf foo'
  [ -n "$output" ]
}

@test "review-guard: an interactive shell is refused however it is reached" {
  # Two review rounds each closed one spelling of this and left the others:
  # first bare `sudo`/`sudo -s`, then the same behind a wrapper. `bash`,
  # `sudo bash` and `su` were allowed throughout — the same write channel by a
  # shorter route. The rule is now stated once over the unwrapped command, so
  # the cases below are one rule rather than five.
  for escape in 'sudo -s' 'sudo -i' 'sudo' 'doas' 'su' 'su -' \
                'bash' 'sh' 'zsh' 'bash -i' 'sudo bash' 'sudo su'; do
    _blocked "$escape"
    [ -n "$output" ] || { echo "allowed: $escape"; false; }
  done
}

@test "review-guard: an interactive shell is refused behind a wrapper" {
  # `env sudo -s` and friends unwrap to the same escape one wrapper removed.
  for escape in 'env sudo -s' 'time sudo -i' 'nohup sudo -s' 'nice sudo -i' \
                'xargs sudo -s' 'env bash' 'env su'; do
    _blocked "$escape"
    [ -n "$output" ] || { echo "allowed: $escape"; false; }
  done
}

# passes-at-base: the guard against over-reach — before INTERACTIVE_SHELLS no shell name was matched at all, so these passed by the hole; the case exists to stop the new rule swallowing `sh -c`, and it fails if the payload exemption is dropped
@test "review-guard: a shell running a read-only payload is still allowed" {
  # The escape rule must not swallow `sh -c`: its payload is a command in its
  # own right, unwrapped and rescanned, so a read stays a read.
  _blocked "bash -c 'pytest tests/'"
  [ -z "$output" ]
  _blocked "sh -c 'grep -rn foo .'"
  [ -z "$output" ]
  _blocked "sudo sh -c 'pytest'"
  [ -z "$output" ]
}

@test "review-guard: every -c spelling is parsed, not refused as an escape" {
  # The escape rule refuses whatever SHELL_DASH_C cannot parse, so a spelling it
  # missed became a false positive rather than an unrecognised read: a
  # path-qualified `/bin/sh -c`, a long flag, a `-c` that is not last in its
  # cluster, and `fish` (in INTERACTIVE_SHELLS but absent from the payload
  # pattern) were all refused while running a plain pytest.
  for ok in "/bin/sh -c 'pytest'" "/bin/bash -c 'pytest tests/'" \
            "bash --norc -c 'pytest'" "bash -ce 'pytest tests/'" \
            "bash -cx 'pytest'" "fish -c 'pytest'"; do
    _blocked "$ok"
    [ -z "$output" ] || { echo "refused a read: $ok ($output)"; false; }
  done
  # The same spellings must still rescan the payload rather than wave it past.
  for bad in "/bin/sh -c 'rm -rf x'" "bash --norc -c 'rm -rf x'" \
             "bash -ce 'rm -rf x'" "fish -c 'rm -rf x'"; do
    _blocked "$bad"
    [ -n "$output" ] || { echo "allowed a write: $bad"; false; }
  done
}

@test "review-guard: a shell-wrapper refusal names what the inner check found" {
  # The recursive shell-payload check used to discard blockedWriteCommand's
  # inner return value and always report a generic message, so the refusal
  # never said what the wrapped command actually did.
  _blocked "bash -c 'rm -rf x'"
  [[ "$output" == *'`rm` writes: rm -rf x'* ]]
}

@test "review-guard: a write behind a command wrapper is refused" {
  # commandHead read one word, so the bare `rm` was refused while every wrapped
  # spelling of it was allowed. A guard that blocks the ergonomic form and
  # permits the awkward one charges turns without containing anything.
  _blocked 'sudo rm -rf /x'
  [ -n "$output" ]
  _blocked 'env rm -rf x'
  [ -n "$output" ]
  _blocked 'time rm -rf x'
  [ -n "$output" ]
  _blocked 'xargs rm -f < list'
  [ -n "$output" ]
  _blocked 'xargs -0 rm -f'
  [ -n "$output" ]
  _blocked "bash -c 'rm -rf x'"
  [ -n "$output" ]
  _blocked "sudo sed -i '' s/a/b/ f"
  [ -n "$output" ]
}

@test "review-guard: a wrapper flag's value is not read as the command" {
  # Skipping a wrapper's flags fails open if a flag takes a separate value: the
  # value lands where the command should be, so `sudo -u root rm -rf x` reads
  # its command as `root` and is allowed. Caught on the first adversarial pass
  # over the wrapper handling, not in review.
  _blocked 'sudo -u root rm -rf x'
  [ -n "$output" ]
  _blocked 'env -u FOO rm -rf x'
  [ -n "$output" ]
  _blocked 'nice -n 5 rm -rf x'
  [ -n "$output" ]
  _blocked 'xargs -I{} rm {}'
  [ -n "$output" ]
  # An attached value consumes no extra word, so the command is still the
  # word after the flag.
  _blocked 'sudo --user=root rm -rf x'
  [ -n "$output" ]
}

@test "review-guard: in-place editors and git writes are refused" {
  _blocked "sed -i '' s/a/b/ f.txt"
  [ -n "$output" ]
  # Not only as the first argument: a cluster, a suffix, and the long spelling.
  _blocked "sed -n -i '' s/a/b/ f.txt"
  [ -n "$output" ]
  _blocked 'sed -i.bak s/a/b/ f.txt'
  [ -n "$output" ]
  _blocked 'sed --in-place s/a/b/ f.txt'
  [ -n "$output" ]
  # GNU sed is gsed on macOS and edits in place just the same.
  _blocked 'gsed -i s/a/b/ f.txt'
  [ -n "$output" ]
  _blocked 'perl -pi -e s/a/b/ f.txt'
  [ -n "$output" ]
  _blocked 'git commit -m x'
  [ -n "$output" ]
  _blocked 'git push'
  [ -n "$output" ]
  _blocked 'curl -o out.bin https://example.com/x'
  [ -n "$output" ]
  # The long spellings write a file and matched nothing before.
  _blocked 'curl --output f https://example.com/x'
  [ -n "$output" ]
  _blocked 'wget --output-document f https://example.com/x'
  [ -n "$output" ]
  _blocked 'curl --remote-name https://example.com/x'
  [ -n "$output" ]
}

@test "review-guard: a git write behind a global flag is refused" {
  # backend_claude.FIX_DENIED_TOOLS names these subcommands and says Pi enforces
  # them here. Anchoring straight to `git\s+commit` made that claim false for
  # any invocation with a flag in front, and the fix engine's accountability
  # rests on it: an agent that commits for itself lands work outside the scope
  # fix.scope watched it produce.
  _blocked 'git -C /repo commit -m x'
  [ -n "$output" ]
  _blocked 'git --no-pager commit -m x'
  [ -n "$output" ]
  _blocked 'git -c user.name=x commit -m y'
  [ -n "$output" ]
  _blocked 'git --git-dir=/r/.git commit -m x'
  [ -n "$output" ]
  _blocked 'git -C /repo push'
  [ -n "$output" ]
}

@test "review-guard: a quoted > is an argument, not a redirect" {
  # The redirect rule read a regex over the raw statement, so any `>` inside a
  # quoted argument was a write target. A review agent greps constantly, and
  # this refused a large share of ordinary reads — comparisons, arrows, type
  # parameters. A guard whose refusals land on greps is one whose refusals stop
  # being read.
  for ok in "awk 'length > 80' f.txt" "awk '\$2 > 100' data" \
            "grep -rn 'a->b' src/" "rg 'fn foo() -> Result' src/" \
            "jq '.items | map(select(.age > 30))' d.json" \
            "git log --pretty='%h -> %s'" "echo 'A > B'"; do
    _blocked "$ok"
    [ -z "$output" ] || { echo "refused a read: $ok ($output)"; false; }
  done
}

@test "review-guard: a redirect that climbs out of a scratch root is refused" {
  # isScratchTarget compared the raw prefix while isScratchPath canonicalised,
  # so one file got opposite answers from two predicates in the same file.
  _blocked 'echo x > /private/tmp/../../Users/isaacg/p.txt'
  [ -n "$output" ]
  _blocked 'pytest > /tmp/../etc/hosts'
  [ -n "$output" ]
  # `>|` is a redirect the old pattern did not know at all.
  _blocked 'echo hi >| /Users/isaacg/probe.txt'
  [ -n "$output" ]
}

# passes-at-base: the old raw-prefix compare also rejected a relative target, so this held before the rewrite; the case exists because routing through isScratchPath introduced cwd resolution, which made every relative path read as scratch until the absolute-only guard was added back
@test "review-guard: a relative redirect target is not scratch" {
  # isScratchPath resolves against the process cwd, so every relative target
  # read as scratch whenever that cwd sat under one of the prefixes — and a
  # review runs in a worktree under /var/folders often enough for that to be
  # the common case. A redirect the guard cannot place must not be exempted.
  _blocked 'echo x > rel.txt'
  [ -n "$output" ]
  _blocked 'echo x > ./rel.txt'
  [ -n "$output" ]
  _blocked 'pytest > ~/notes.txt'
  [ -n "$output" ]
}

@test "review-guard: a redirect outside the scratch roots is refused" {
  _blocked 'echo hi > /etc/hosts'
  [ -n "$output" ]
  _blocked 'pytest > ~/notes.txt'
  [ -n "$output" ]
}

@test "review-guard: a second redirect outside the scratch roots is refused" {
  # REDIRECT.exec() only ever saw the first redirect in a statement, so
  # `pytest > /tmp/out.txt 2>/etc/badfile` was judged solely on the scratch
  # first redirect and the non-scratch second one was never checked.
  _blocked 'pytest > /tmp/out.txt 2>/etc/badfile'
  [ -n "$output" ]
  [[ "$output" == *"/etc/badfile"* ]]
}

@test "review-guard: the refusal names the offending statement" {
  # A 120-char slice of the whole command hid the trailing redirect that was
  # the real match, so the refusal read as though it had blocked the cd.
  _blocked 'cd /some/very/long/worktree/path/that/runs/past/the/old/truncation/limit/for/sure/and/then/some && pytest tests/ > /etc/out.txt'
  [ -n "$output" ]
  [[ "$output" == *"/etc/out.txt"* ]]
}

# _scratch PATH — prints "true" when write and edit may target PATH.
_scratch() {
  run node --input-type=module -e "
    const { isScratchPath } = await import('$REPO_ROOT/ai/pi/extensions-cli/detect.ts');
    process.stdout.write(String(isScratchPath(process.argv[1])));
  " -- "$1"
}

@test "review-guard: the write tool may create a scratch file under /tmp" {
  # The redirect rule has always exempted these roots, and withholding them
  # from the write tool left the guard contradicting itself: an agent needing a
  # scratch file could only create one inside the worktree, and then could not
  # remove it because rm, mv, cp and git clean are all refused. Untracked files
  # are in the commit scope, so the leftovers were committed and pushed.
  _scratch /tmp/probe.test.ts
  [ "$output" = true ]
  _scratch /private/tmp/probe.test.ts
  [ "$output" = true ]
  _scratch /var/folders/ab/cd/T/probe.test.ts
  [ "$output" = true ]
}

@test "review-guard: a path that climbs out of a scratch root is not scratch" {
  # The exemption is resolved, not a prefix compare: a startsWith check reads
  # /tmp/../etc/hosts as being under /tmp, which turns the scratch allowance
  # into a write anywhere on the filesystem.
  _scratch /tmp/../etc/hosts
  [ "$output" = false ]
  _scratch /tmp/a/../../etc/passwd
  [ "$output" = false ]
  # A `.` segment resolves the other way and stays inside.
  _scratch /tmp/./probe.test.ts
  [ "$output" = true ]
  _scratch /tmpfoo/x
  [ "$output" = false ]
  _scratch /Users/i/wt/src.py
  [ "$output" = false ]
}

@test "review-guard: the scratch roots match claude-bash-guard's" {
  # Two guards enforcing one rule that disagree is worse than one guard.
  for root in /tmp/ /private/tmp/ /var/folders/; do
    run grep -q -- "$root" "$REPO_ROOT/ai/pi/extensions-cli/detect.ts"
    [ "$status" -eq 0 ]
    run grep -q -- "$root" "$REPO_ROOT/ai/claude/bin/claude-bash-guard"
    [ "$status" -eq 0 ]
  done
}

@test "review-guard: no context hook, so no marker to keep in step" {
  # --no-skills already suppresses the superpowers bootstrap, which the package
  # injects only for skills it discovered. A filter here matched nothing on
  # every probe; one added later would be a hook that never fires.
  run grep -q 'pi.on("context"' "$REPO_ROOT/ai/pi/extensions-cli/review-guard.ts"
  [ "$status" -ne 0 ]
}

@test "review-guard: the bare flags do not disable extension discovery" {
  # --no-extensions would deregister the provider serving the run and strip
  # every gh_*/web_* tool from the agent's list.
  run grep -q 'no-extensions' "$REPO_ROOT/ai/lib/agent/backend_pi.py"
  [ "$status" -eq 0 ]
  run grep -qE 'BARE_FLAGS = \("--no-context-files", "--no-skills"\)' \
    "$REPO_ROOT/ai/lib/agent/backend_pi.py"
  [ "$status" -eq 0 ]
}
