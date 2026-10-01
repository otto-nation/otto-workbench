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
    *"task pr:create"*) echo pr-create ;;
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

# ── tree-lock-guard ──────────────────────────────────────────────────────────
# Same split as issue-defer-guard: the predicate is in detect.ts, which imports
# nothing but node builtins, so node can load it directly. index.ts is the
# wiring no test can reach.
#
# The probe is with-tree-lock --check, which is is_locked(). Tests hold a real
# flock rather than stubbing the CLI, so an inverted probe fails here the same
# way it fails in tests/tree_lock_test.py.

# _lock_refusal FILE — prints the refusal string, or "null".
_lock_refusal() {
  run node --input-type=module -e "
    const { lockRefusal } = await import('$REPO_ROOT/ai/pi/extensions/tree-lock-guard/detect.ts');
    const msg = lockRefusal(process.argv[1]);
    process.stdout.write(msg === null ? 'null' : msg);
  " -- "$1"
}

# _hold_tree lives in tests/test_helper.bash: claude_settings.bats holds the
# same lock for the Claude half of this guard, and one fact read by two
# harnesses is worth one helper rather than two copies of it.

@test "tree-lock-guard: a free tree is not a refusal" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  git -C "$repo" init -q -b feat
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  git -C "$repo" commit -q --allow-empty -m init
  touch "$repo/file.txt"
  _lock_refusal "$repo/file.txt"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "tree-lock-guard: a held tree is a refusal that names no pid" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  git -C "$repo" init -q -b feat
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  git -C "$repo" commit -q --allow-empty -m init
  touch "$repo/file.txt"
  local holder
  holder="$(_hold_tree "$repo")"
  _lock_refusal "$repo/file.txt"
  local result_status=$status result_out=$output
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [ "$result_status" -eq 0 ]
  [[ "$result_out" == *"A validator holds this tree"* ]]
  [[ "$result_out" != *[Pp]id* ]]
}

@test "tree-lock-guard: WORKBENCH_TREE_LOCK does not suppress the refusal" {
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  git -C "$repo" init -q -b feat
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  git -C "$repo" commit -q --allow-empty -m init
  touch "$repo/file.txt"
  local holder
  holder="$(_hold_tree "$repo")"
  WORKBENCH_TREE_LOCK="$repo" _lock_refusal "$repo/file.txt"
  local result_out=$output
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [[ "$result_out" == *"A validator holds this tree"* ]]
}

@test "tree-lock-guard: inherited GIT_DIR does not retarget the probe" {
  local repo="$TMPDIR/repo" other="$TMPDIR/other"
  mkdir -p "$repo" "$other"
  git -C "$repo" init -q -b feat
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  git -C "$repo" commit -q --allow-empty -m init
  touch "$repo/file.txt"
  git -C "$other" init -q -b feat
  git -C "$other" config user.email t@t
  git -C "$other" config user.name t
  git -C "$other" commit -q --allow-empty -m init
  local holder other_git
  holder="$(_hold_tree "$repo")"
  other_git=$(git -C "$other" rev-parse --absolute-git-dir)
  GIT_DIR="$other_git" GIT_WORK_TREE="$other" _lock_refusal "$repo/file.txt"
  local result_out=$output
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  [[ "$result_out" == *"A validator holds this tree"* ]]
}

@test "tree-lock-guard: a torn record still refuses without naming a pid" {
  # holders() can be [] while is_locked() is true. Take LOCK_SH without
  # writing a JSONL line, which is the state acquire()'s _record can tear into.
  local repo="$TMPDIR/repo"
  mkdir -p "$repo"
  git -C "$repo" init -q -b feat
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  git -C "$repo" commit -q --allow-empty -m init
  touch "$repo/file.txt"
  local git_dir lock
  git_dir=$(git -C "$repo" rev-parse --absolute-git-dir)
  lock="$git_dir/workbench-validate.lock"
  python3 -c "
import fcntl, sys, time
h = open(sys.argv[1], 'a+')
fcntl.flock(h, fcntl.LOCK_SH)
time.sleep(30)
" "$lock" >/dev/null 2>&1 &
  local py=$! locked=""
  for _ in $(seq 1 50); do
    if "$REPO_ROOT/bin/local/with-tree-lock" --check "$repo" >/dev/null 2>&1; then
      locked=yes
      break
    fi
    sleep 0.1
  done
  # Fail here rather than at the assertion below: an unlocked tree makes
  # _lock_refusal correctly return null, and the final assertion would report
  # a guard that failed to refuse instead of a lock that was never taken.
  if [[ -z "$locked" ]]; then
    kill "$py" 2>/dev/null || true
    echo "tree $repo never became locked" >&2
    return 1
  fi
  _lock_refusal "$repo/file.txt"
  local result_out=$output
  kill "$py" 2>/dev/null || true
  wait "$py" 2>/dev/null || true
  [[ "$result_out" == *"A validator holds this tree"* ]]
  [[ "$result_out" != *[Pp]id* ]]
}

@test "tree-lock-guard: missing path or non-git path fails open" {
  # The status is asserted alongside the output: a node crash whose message
  # happened to contain "null" would otherwise pass the output check alone.
  _lock_refusal "$TMPDIR/no-such-parent/file.txt"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
  mkdir -p "$TMPDIR/plain"
  _lock_refusal "$TMPDIR/plain/file.txt"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "tree-lock-guard: detect.ts imports no SDK" {
  run grep -E '@earendil-works/pi-coding-agent|isToolCallEventType' \
    "$REPO_ROOT/ai/pi/extensions/tree-lock-guard/detect.ts"
  [ "$status" -ne 0 ]
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
    const { bypassesTheCommitScope } = await import('$REPO_ROOT/ai/pi/extensions-cli/detect.ts');
    process.stdout.write(bypassesTheCommitScope(process.argv[1]) ?? '');
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

@test "review-guard: an ordinary command is not read as a git write" {
  # \b treats / and - as boundaries, so an unanchored match caught the branch
  # name and disabled bash for the whole session.
  _blocked 'cd /Users/i/wt/isaac-fix-rm-stale && pytest tests/'
  [ -z "$output" ]
  _blocked 'cd /Users/i/wt/repo-install-hooks && pytest'
  [ -z "$output" ]
  _blocked 'pytest tests/install/test_setup.py'
  [ -z "$output" ]
  _blocked 'grep -rn tee ai/'
  [ -z "$output" ]
}

@test "review-guard: a filesystem write is permitted, and that is the design" {
  # This predicate is not a security boundary and must not be read as one. A
  # fix agent holds an unrestricted bash, so `python3 -c` and `find -delete`
  # write freely; an earlier version grew five lists of write verbs, shells
  # and wrappers trying to catch them and blocked only the spellings an agent
  # reaches for by accident.
  #
  # What contains the agent is elsewhere: write/edit are path-gated, the engine
  # commits only the paths it watched, and the push is gated. This case exists
  # so that a future reader adding `rm` back has to delete an assertion that
  # says why it is absent, rather than filing the allowance as a bug.
  for permitted in 'rm -rf build' 'touch f' 'sudo -s' 'bash' \
                   'sed -i "" s/a/b/ f' 'curl -o out.bin https://x' \
                   'python3 -c "import os; os.remove(0)"' 'find . -delete'; do
    _blocked "$permitted"
    [ -z "$output" ] || { echo "refused, but this guard does not own that: $permitted"; false; }
  done
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

# passes-at-base: #1480 landed the global-flag reach in main, so this holds without the trim; it stays because the rewrite to token comparison had to preserve it, and it fails if the flag-skipping loop is dropped
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

# passes-at-base: reported by check-new-tests because the whole file moved, but verified against origin/main directly — `git add -A` is allowed there, so this case does fail without the change
@test "review-guard: the git subcommands that stage and move refs are writes" {
  # `git rm -rf .` deletes the worktree and stages the deletion, and was
  # allowed while a bare `rm -rf .` was refused.
  for bad in 'git add -A' 'git rm -rf .' 'git mv a b' 'git branch -D x' \
             'git tag -f v1' 'git config user.name x' \
             'git worktree add /tmp/w'; do
    _blocked "$bad"
    [ -n "$output" ] || { echo "allowed: $bad"; false; }
  done
}

@test "review-guard: -n is a dry run for some subcommands and not for others" {
  # `-n` is --dry-run for push, clean, add and merge, but --no-verify for
  # commit. Treating it as read-only everywhere let `git commit -n -m x`
  # through, which makes a real commit — verified against a scratch repo, not
  # inferred from the manual.
  for bad in 'git commit -n -m x' 'git commit -nm x' 'git commit --no-verify -m x'; do
    _blocked "$bad"
    [ -n "$output" ] || { echo "allowed a commit: $bad"; false; }
  done
  for ok in 'git push -n' 'git clean -n' 'git add -n f' 'git merge -n topic'; do
    _blocked "$ok"
    [ -z "$output" ] || { echo "refused a dry run: $ok"; false; }
  done
}

@test "review-guard: a commit reached through a wrapper is refused" {
  # The recursion that catches these served the git rule too, not only the
  # write-verb rules removed alongside it — cutting it silently reopened
  # `bash -c 'git commit'`. A wrapped commit lands outside the engine's scope
  # as squarely as a bare one.
  for bad in "bash -c 'git commit -m x'" "eval 'git commit -m x'" \
             'eval git commit -m x' "sh -c \"sh -c 'git push'\"" \
             "bash -ce 'git add -A'"; do
    _blocked "$bad"
    [ -n "$output" ] || { echo "allowed: $bad"; false; }
  done
  # Rescanned, not refused on sight: a wrapper around a read is still a read,
  # and a wrapper around a plain filesystem write is not this guard's business.
  for ok in "bash -c 'pytest tests/'" "eval 'pytest'" \
            "eval awk 'length > 80' f" "bash -c 'rm -rf x'"; do
    _blocked "$ok"
    [ -z "$output" ] || { echo "refused: $ok ($output)"; false; }
  done
}

@test "review-guard: a read-only git subcommand is allowed" {
  # `\b` after the subcommand made `merge` match `merge-base` — the same
  # hyphen-boundary bug WRITE_COMMANDS warns about. `git merge-base` is how a
  # review establishes its base, and ai/lib calls it in fifteen places.
  for ok in 'git merge-base origin/main HEAD' 'git merge-base --is-ancestor A B' \
            'git merge-tree a b' 'git stash list' 'git stash show -p' \
            'git apply --check p.diff' 'git clean -n' 'git push --dry-run' \
            'git branch --list' 'git config --get user.name' \
            'git remote -v' 'git worktree list'; do
    _blocked "$ok"
    [ -z "$output" ] || { echo "refused a read: $ok ($output)"; false; }
  done
}

@test "review-guard: a command with an unbalanced quote is refused" {
  # The scan cannot see where such a command ends, so every rule is reading
  # fragments rather than the command. tokenize.ts documented `hasUnparsed` as
  # the thing a caller should consult and nothing consulted it — a promise the
  # code did not keep. Bash rejects most of these before running anything, so
  # this is a small hole rather than a live bypass, but a predicate that cannot
  # read its input must not answer "allow".
  _blocked "echo 'unterminated; rm -rf x"
  [ -n "$output" ]
  _blocked "bash -c 'rm -rf x"
  [ -n "$output" ]
  # A balanced quote spanning two lines is readable, and stays readable.
  _blocked "$(printf "echo 'a\nb'\npytest tests/")"
  [ -z "$output" ]
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

# passes-at-base: same as above — verified against origin/main, which allows `> /private/tmp/../../Users/isaacg/p.txt`, so this case does fail without the change
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
  # $HOME is unexpanded here the same way ~ is, and for the same reason: this
  # file does not expand either, so both fall to the same refusal rather than
  # being read as scratch.
  _blocked 'pytest > $HOME/notes.txt'
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

# _unscoped COMMAND — prints the unscoped-runner refusal, or the empty string.
_unscoped() {
  run node --input-type=module -e "
    const { unscopedTestRun } = await import('$REPO_ROOT/ai/pi/extensions-cli/detect.ts');
    process.stdout.write(unscopedTestRun(process.argv[1]) ?? '');
  " -- "$1"
}

@test "review-guard: an unscoped pytest is refused" {
  _unscoped 'pytest'
  [ -n "$output" ]
  [[ "$output" == *"invoke it directly"* ]]
  [[ "$output" == *"pytest tests/foo.py"* ]]
  _unscoped 'pytest -q'
  [ -n "$output" ]
  _unscoped 'pytest -v --tb=short'
  [ -n "$output" ]
}

@test "review-guard: pytest with a path is allowed" {
  _unscoped 'pytest tests/foo.py'
  [ -z "$output" ]
  _unscoped 'pytest tests/'
  [ -z "$output" ]
  _unscoped 'pytest -q tests/foo.py'
  [ -z "$output" ]
  _unscoped 'pytest tests/foo.py > /tmp/out.txt 2>&1'
  [ -z "$output" ]
}

@test "review-guard: run-tests and validate-all are refused" {
  # These wrappers exist to run the project's gate, which pre-push reproduces.
  # A path flag does not make them the cheap named-test form.
  _unscoped 'bin/local/run-tests'
  [ -n "$output" ]
  [[ "$output" == *"invoke it directly"* ]]
  _unscoped './bin/local/run-tests --bats --files tests/foo.bats'
  [ -n "$output" ]
  _unscoped 'bin/local/validate-all'
  [ -n "$output" ]
  _unscoped 'validate-all --quiet'
  [ -n "$output" ]
}

@test "review-guard: a selector flag or node id is scoped" {
  _unscoped 'pytest -k test_foo'
  [ -z "$output" ]
  _unscoped 'pytest --last-failed'
  [ -z "$output" ]
  _unscoped 'pytest --lf'
  [ -z "$output" ]
  _unscoped 'pytest tests/foo.py::test_bar'
  [ -z "$output" ]
  _unscoped 'bats --filter some_case'
  [ -z "$output" ]
  _unscoped 'bats -f some_case'
  [ -z "$output" ]
}

@test "review-guard: pytest has no --keyword long form, only -k" {
  # pytest's own --help lists only "-k EXPRESSION"; there is no --keyword
  # spelling. Listing it in SUBJECT_RUNNERS would let an agent "scope" a run
  # with a flag pytest itself rejects, which is not scoping at all. A
  # trailing word is left off: `--keyword test_foo` reads test_foo as a
  # positional subject regardless of the flag, which would mask this case.
  _unscoped 'pytest --keyword'
  [ -n "$output" ]
  [[ "$output" == *"invoke it directly"* ]]
}

@test "review-guard: a selector flag belongs to one runner, not both" {
  # -f is --filter to bats and --looponfail to pytest, which re-runs the whole
  # suite on every file change. Reading it as a subject would allow the worst
  # case this predicate exists to refuse.
  _unscoped 'pytest -f'
  [ -n "$output" ]
  # --lf is pytest's; bats has no such flag, so it names nothing there.
  _unscoped 'bats --lf'
  [ -n "$output" ]
}

@test "review-guard: unscoped bats is refused, bats with a file is allowed" {
  _unscoped 'bats'
  [ -n "$output" ]
  [[ "$output" == *"invoke it directly"* ]]
  _unscoped 'bats tests/foo.bats'
  [ -z "$output" ]
}

@test "review-guard: a wrapped unscoped pytest is refused" {
  _unscoped "bash -c 'pytest'"
  [ -n "$output" ]
  _unscoped "bash -c 'pytest tests/foo.py'"
  [ -z "$output" ]
}

@test "review-guard: the unscoped-runner predicate is the one review-guard calls" {
  # review-guard.ts imports the SDK and cannot be loaded here. The wiring is
  # the one thing this file cannot assert by running the function.
  run grep -q 'unscopedTestRun(event.input.command)' \
    "$REPO_ROOT/ai/pi/extensions-cli/review-guard.ts"
  [ "$status" -eq 0 ]
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

# ─── job-poll-guard ────────────────────────────────────────────────────────
# Refuses a second read of a job this agent run already polled. Same split as
# the other guards: detect.ts imports nothing, so every branch runs under a
# bare node.
#
# The predicate cannot ask whether a job is running — the jobs tools are
# another repo's, their manager is closure-local, and tool_call fires before
# execute. So the tests drive the sequence the guard actually sees: calls, and
# the text their results came back with.

# _poll OPS — run a sequence against one state and print each pollRefusal.
#
# OPS is a JSON array of steps, each one of:
#   {"call": "job_output", "id": "job-1"}   a guarded call; prints REFUSED or ok
#   {"result": "job_output", "id": "job-1", "text": "...", "isError": false}
#   {"reset": true}                          what agent_start does
_poll() {
  run node --input-type=module -e "
    const d = await import('$REPO_ROOT/ai/pi/extensions/job-poll-guard/detect.ts');
    const state = d.freshState();
    const out = [];
    for (const step of JSON.parse(process.argv[1])) {
      if (step.reset) { d.resetState(state); continue; }
      if (step.result) {
        d.noteResult(state, step.result, step.text ?? '', step.isError ?? false);
        continue;
      }
      const reason = d.pollRefusal(state, step.call, step.id);
      if (reason === null) { d.noteCall(state, step.call, step.id); out.push('ok'); }
      else out.push('REFUSED');
    }
    process.stdout.write(out.join(','));
  " -- "$1"
}

_running_line() { printf '%s  [running 12s]  a job\n\nsome output' "$1"; }
_exited_line() { printf '%s  [exit 0 after 12s]  a job\n\nsome output' "$1"; }

@test "job-poll-guard: the first job_output of a run is allowed" {
  _poll '[{"call":"job_output","id":"job-1"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "job-poll-guard: a repeat with no result yet is refused" {
  # Two job_output calls in one parallel batch both reach tool_call before
  # either result lands. That is the repeat with the least excuse.
  _poll '[{"call":"job_output","id":"job-1"},{"call":"job_output","id":"job-1"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED" ]
}

@test "job-poll-guard: polling a different job is not a repeat" {
  _poll '[{"call":"job_output","id":"job-1"},{"call":"job_output","id":"job-2"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
}

@test "job-poll-guard: a still-running result refuses the next read" {
  local line
  line=$(_running_line job-1)
  _poll "$(printf '[{"call":"job_output","id":"job-1"},{"result":"job_output","text":%s},{"call":"job_output","id":"job-1"}]' "$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")"
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED" ]
}

@test "job-poll-guard: a finished job may be read again for more output" {
  # The tool's own description says to raise max_bytes for more, so a second
  # read of a job that has exited is the documented use, not a poll.
  local line
  line=$(_exited_line job-1)
  _poll "$(printf '[{"call":"job_output","id":"job-1"},{"result":"job_output","text":%s},{"call":"job_output","id":"job-1"}]' "$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")"
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
}

@test "job-poll-guard: a killed or failed job reads as finished" {
  _poll '[{"call":"job_output","id":"j"},{"result":"job_output","text":"j  [killed after 3s]  x"},{"call":"job_output","id":"j"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
  _poll '[{"call":"job_output","id":"j"},{"result":"job_output","text":"j  [failed after 3s]  x"},{"call":"job_output","id":"j"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
}

@test "job-poll-guard: an unparseable result fails open" {
  # The line format belongs to another repo. A guard that refused on text it
  # could not read would block every poll the day that format changed.
  _poll '[{"call":"job_output","id":"job-1"},{"result":"job_output","text":"something else entirely"},{"call":"job_output","id":"job-1"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED" ]
}

@test "job-poll-guard: an errored result does not refuse the corrected retry" {
  # `No such job: x` is a typo, not a poll.
  _poll '[{"call":"job_output","id":"job-1"},{"result":"job_output","text":"No such job: job-1","isError":true},{"call":"job_output","id":"job-1"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
}

@test "job-poll-guard: the first job_list is allowed and a bare repeat is not" {
  _poll '[{"call":"job_list"},{"call":"job_list"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED" ]
}

@test "job-poll-guard: a list showing a running job refuses the next list" {
  _poll '[{"call":"job_list"},{"result":"job_list","text":"2 job(s), 1 running:\nj  [running 4s]  x"},{"call":"job_list"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED" ]
}

@test "job-poll-guard: a list with nothing running may be repeated" {
  _poll '[{"call":"job_list"},{"result":"job_list","text":"No background jobs."},{"call":"job_list"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
  _poll '[{"call":"job_list"},{"result":"job_list","text":"2 job(s), 0 running:\nj  [exit 0 after 4s]  x"},{"call":"job_list"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok" ]
}

@test "job-poll-guard: the two tools do not count against each other" {
  _poll '[{"call":"job_output","id":"job-1"},{"call":"job_list"},{"call":"job_output","id":"job-2"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,ok,ok" ]
}

@test "job-poll-guard: a reset allows a job refused before it" {
  # This is the agent_start contract. A job's completion is delivered as
  # nextTurn, so the read that follows the notice lands in a later agent run
  # and must not be refused as a repeat.
  _poll '[{"call":"job_output","id":"job-1"},{"call":"job_output","id":"job-1"},{"reset":true},{"call":"job_output","id":"job-1"}]'
  [ "$status" -eq 0 ]
  [ "$output" = "ok,REFUSED,ok" ]
}

# _parse_out TEXT / _parse_list TEXT — the two text readers, as JSON.
_parse_out() {
  run node --input-type=module -e "
    const { parseOutputStatus } = await import('$REPO_ROOT/ai/pi/extensions/job-poll-guard/detect.ts');
    process.stdout.write(JSON.stringify(parseOutputStatus(process.argv[1])));
  " -- "$1"
}

_parse_list() {
  run node --input-type=module -e "
    const { parseListRunningCount } = await import('$REPO_ROOT/ai/pi/extensions/job-poll-guard/detect.ts');
    process.stdout.write(JSON.stringify(parseListRunningCount(process.argv[1])));
  " -- "$1"
}

@test "job-poll-guard: the status reader matches formatJobLine's real shapes" {
  # Shapes taken from formatJobLine in usemaximum/pi-extensions extensions/jobs.
  _parse_out "job-1  [running 12s]  run the suite"
  [ "$status" -eq 0 ]
  [ "$output" = '{"id":"job-1","status":"running"}' ]
  _parse_out "job-2  [exit 0 after 198s]  run the suite"
  [ "$status" -eq 0 ]
  [ "$output" = '{"id":"job-2","status":"done"}' ]
  _parse_out "job-3  [killed after 3s]  x"
  [ "$status" -eq 0 ]
  [ "$output" = '{"id":"job-3","status":"done"}' ]
  _parse_out "no brackets here"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "job-poll-guard: the list reader counts what is running" {
  _parse_list "3 job(s), 2 running:"
  [ "$status" -eq 0 ]
  [ "$output" = "2" ]
  _parse_list "No background jobs."
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
  _parse_list "something else"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "job-poll-guard: detect.ts imports no SDK" {
  run grep -E '@earendil-works/pi-coding-agent|isToolCallEventType' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/detect.ts"
  [ "$status" -ne 0 ]
}

@test "job-poll-guard: index.ts resets on agent_start, not turn_start" {
  # A turn_start reset would only catch two polls in one assistant message and
  # would refuse the legitimate read that follows a completion notice.
  run grep -q 'pi.on("agent_start"' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -eq 0 ]
  run grep -q 'resetState(state)' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -eq 0 ]
  # Scoped to a subscription, since the header comment explains at length why
  # turn_start is the wrong event and would match a bare grep for the word.
  run grep -q 'pi.on("turn_start"' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -ne 0 ]
}

@test "job-poll-guard: index.ts decides through the predicate and records results" {
  # A hand-written reason at the call site would pass the refusal tests above
  # while the wiring bypassed the predicate entirely.
  run grep -q 'pollRefusal(state' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -eq 0 ]
  run grep -q 'pi.on("tool_result"' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -eq 0 ]
  run grep -q 'noteResult(' \
    "$REPO_ROOT/ai/pi/extensions/job-poll-guard/index.ts"
  [ "$status" -eq 0 ]
}

# ── issue-capture ────────────────────────────────────────────────────────────
#
# The recording half of the filing rule. Its refusing half is issue-defer-guard
# above; both harnesses have both, and ai/bin/record-filed-issue is the one
# writer they share.

# _captures COMMAND — prints true or false for isIssueFiling(COMMAND).
_captures() {
  run node --input-type=module -e "
    const { isIssueFiling } = await import('$REPO_ROOT/ai/pi/extensions/issue-capture/detect.ts');
    console.log(isIssueFiling(process.argv[1]));
  " "$1"
}

@test "issue-capture: a filing is recognised, a mention of one is not" {
  _captures 'gh issue create --title x'
  [ "$output" = true ]
  _captures 'cd /x && gh issue create'
  [ "$output" = true ]
  _captures 'echo gh issue create'
  [ "$output" = false ]
  _captures 'gh issue list'
  [ "$output" = false ]
}

@test "issue-capture: the two harnesses recognise the same filings" {
  # The Claude side is the same regex in the recorder and in the PreToolUse
  # guard. A command one harness records and the other does not is a filing
  # that reaches the ledger from one seat and not the other.
  local cmd
  for cmd in \
    'gh issue create --title x' \
    'echo gh issue create' \
    'gh issue list'; do
    _captures "$cmd"
    local pi="$output"
    _files_issue "$cmd"
    [ "$pi" = "$output" ] || {
      echo "capture and defer-guard disagree on: $cmd"
      echo "  issue-capture=$pi  issue-defer-guard=$output"
      return 1
    }
  done
}

@test "issue-capture: the bash regex and both TS matchers agree on the same commands" {
  # The two TS matchers are checked against each other above. The bash side
  # reached from Claude's PostToolUse hook (record-filed-issue's own
  # re_issue_create) and the PreToolUse guard (claude-bash-guard's copy of the
  # same variable) is a third, independent implementation of "is this a filing",
  # kept in sync with the other two only by a code comment — a bash-vs-TS drift
  # here is exactly the three-way drift this epic's guards used to suffer from,
  # and none of the tests above would catch it.
  local re_bash
  re_bash=$(grep -m1 "^re_issue_create=" "$REPO_ROOT/ai/bin/record-filed-issue")
  local re_guard
  re_guard=$(grep -m1 "^re_issue_create=" "$REPO_ROOT/ai/claude/bin/claude-bash-guard")
  [ "$re_bash" = "$re_guard" ]

  local cmd
  for cmd in \
    'gh issue create --title x' \
    'cd /x && gh issue create' \
    'echo gh issue create' \
    'gh issue list'; do
    # Sourced from the script rather than restated: a copy here would be a
    # fourth spelling, and the test would pass while the three real ones drift.
    # Unwrapped into a local of this test's own naming rather than eval'd into
    # the script's variable name, which shellcheck cannot see being assigned.
    local pattern="${re_bash#re_issue_create=}"
    pattern="${pattern#\'}"
    pattern="${pattern%\'}"
    local bash_result=false
    [[ "$cmd" =~ $pattern ]] && bash_result=true

    _captures "$cmd"
    local pi="$output"
    [ "$bash_result" = "$pi" ] || {
      echo "bash re_issue_create and issue-capture disagree on: $cmd"
      echo "  bash=$bash_result  issue-capture=$pi"
      return 1
    }
  done
}

@test "issue-capture: an ambiguous response records nothing rather than a guess" {
  # `gh issue create && gh issue view 5` prints two URLs, and picking one by
  # position records whichever the chain ended with — attributing someone
  # else's issue to this filing. A ledger entry pointing at the wrong issue is
  # worse than a missing one: it reads as a record somebody checked.
  #
  # The ledger is seeded first, and the assertion is that it still holds only
  # the seeded entry. Asserting "no state file" would pass on an empty sandbox
  # whatever the recorder did, which is no assertion at all.
  local sandbox="$BATS_TEST_TMPDIR/amb"
  mkdir -p "$sandbox/repo" "$sandbox/state"
  git -C "$sandbox/repo" init -q -b feat/amb
  git -C "$sandbox/repo" remote add origin git@github.com:otto-nation/otto-workbench.git
  git -C "$sandbox/repo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init

  run env WORKBENCH_STATE_DIR="$sandbox/state" python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + "/ai/lib")
from pathlib import Path
from pr import state as s, target as t
d = t.target_dir_for_checkout(Path(sys.argv[2]))
d.mkdir(parents=True, exist_ok=True)
s.save_state(d, s.new_state(repo="otto-nation/otto-workbench", branch="feat/amb",
                            pr_number=1, head_sha="abc", worktree_root=sys.argv[2]))
print(d)
' "$REPO_ROOT" "$sandbox/repo"
  [ "$status" -eq 0 ]
  local target="$output"

  local payload
  payload=$(python3 -c '
import json
print(json.dumps({
    "tool_input": {"command": "gh issue create --title x && gh issue view 5"},
    "tool_response": {"stdout": "https://github.com/o/r/issues/99\nhttps://github.com/o/r/issues/5\n"},
}))')

  run env WORKBENCH_STATE_DIR="$sandbox/state" \
    bash -c "cd '$sandbox/repo' && printf '%s' '$payload' | '$REPO_ROOT/ai/bin/record-filed-issue'"
  [ "$status" -eq 0 ]

  # The seeded ledger is untouched: no entry, rather than an entry naming 5.
  run python3 -c '
import sys
sys.path.insert(0, sys.argv[1] + "/ai/lib")
from pathlib import Path
from pr import state as s
st = s.load_state(Path(sys.argv[2]))
print(",".join(e.ref.id for e in st.follow_ups.entries))
' "$REPO_ROOT" "$target"
  [ "$output" = "" ]
}

@test "issue-capture: the url is read from what gh printed, or nothing is" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/pi/extensions/issue-capture/detect.ts');
    console.log(JSON.stringify([
      m.filedIssueUrl('https://github.com/o/r/issues/12'),
      m.filedIssueUrl('error: could not create issue'),
      m.issueIdFrom('https://github.com/o/r/issues/12'),
    ]));
  "
  [ "$output" = '["https://github.com/o/r/issues/12",null,"12"]' ]
}

@test "issue-capture: detect.ts imports no SDK" {
  # Same contract as the other predicates: bats loads this under bare node.
  #
  # Matched on `import` lines rather than anywhere in the file. The older twins
  # of this test grep the whole text, which also fires on a header comment that
  # merely explains why the SDK is absent — a file cannot document the rule it
  # obeys without failing the check for it.
  run grep -nE '^\s*import .*(@earendil-works/pi-coding-agent|isToolCallEventType)' \
    "$REPO_ROOT/ai/pi/extensions/issue-capture/detect.ts"
  [ "$status" -ne 0 ]
}

@test "issue-capture: the recorder is handed its payload on stdin" {
  # `execFile` has no `input` option. Passing one leaves the recorder blocked on
  # a stdin that never closes and the hook hangs — which is not a crash, so
  # nothing reports it: the filing simply never reaches the ledger. Asserted on
  # the wiring because the failure is invisible in the extension's own output.
  local index="$REPO_ROOT/ai/pi/extensions/issue-capture/index.ts"
  grep -q 'child.stdin?.end(' "$index"
  run grep -nE 'execFile\([^)]*\binput:' "$index"
  [ "$status" -ne 0 ]

  # And the real shape round-trips: a payload written to stdin reaches a reader
  # that blocks on `cat`, and the callback fires.
  run node --input-type=module -e "
    import { execFile } from 'node:child_process';
    const child = execFile('bash', ['-c', 'cat'], { timeout: 5000 },
      (err, stdout) => { console.log(err ? 'ERR' : stdout.trim()); });
    child.stdin.end('{\"ok\":1}');
  "
  [ "$output" = '{"ok":1}' ]
}

@test "issue-capture: both harnesses write through the one recorder" {
  # Two copies of "what a ledger entry looks like" would drift the way the
  # guards did before they shared a scan.
  grep -q 'record-filed-issue' "$REPO_ROOT/ai/pi/extensions/issue-capture/index.ts"
  grep -q 'record-filed-issue' "$REPO_ROOT/ai/claude/settings.json"
}

# ── record-filed-issue ───────────────────────────────────────────────────────
#
# The shared writer both harnesses hand a payload to. Exercised directly here
# (rather than through either extension) because the URL-attribution question
# below is about the writer's own parsing, not about either hook's wiring.

_record_filed_issue_setup() {
  RFI_REPO="$TMPDIR/rfi-repo"
  mkdir -p "$RFI_REPO"
  git -C "$RFI_REPO" init -q -b main
  git -C "$RFI_REPO" remote add origin git@github.com:acme/widget.git
  git -C "$RFI_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init

  RFI_TARGET=$(python3 - "$REPO_ROOT" "$RFI_REPO" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "ai" / "lib"))
from pr import target as pr_target
print(pr_target.target_dir_for_checkout(Path(sys.argv[2])))
PY
)
  mkdir -p "$RFI_TARGET"
  cat > "$RFI_TARGET/state.json" <<EOF
{"_version": 1, "identity": {"repo": "acme/widget", "branch": "main", "pr_number": null, "head_sha": "abc123", "worktree_root": "$RFI_REPO"}, "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-01T00:00:00Z"}
EOF
}

_rfi_entry_count() {
  python3 -c "
import json
with open('$RFI_TARGET/state.json') as f:
    d = json.load(f)
print(len(d.get('follow_ups', {}).get('entries', [])))
"
}

@test "record-filed-issue: a single issue URL is recorded" {
  _record_filed_issue_setup
  cd "$RFI_REPO"
  payload='{"tool_input":{"command":"gh issue create --title x"},"tool_response":{"stdout":"https://github.com/acme/widget/issues/42"}}'
  run bash -c "printf '%s' '$payload' | '$REPO_ROOT/ai/bin/record-filed-issue'"
  [ "$status" -eq 0 ]
  [ "$(_rfi_entry_count)" = "1" ]
  grep -q '"id": "42"' "$RFI_TARGET/state.json"
}

@test "record-filed-issue: more than one URL-shaped match records nothing rather than guess" {
  # Nothing here tells the hook which of the two matches this `gh issue
  # create` actually filed, so it must not guess with `tail -1` and risk
  # attributing the wrong issue to this filing.
  _record_filed_issue_setup
  cd "$RFI_REPO"
  payload='{"tool_input":{"command":"gh issue create --title x"},"tool_response":{"stdout":"https://github.com/acme/widget/issues/42\nhttps://github.com/acme/widget/issues/99"}}'
  run bash -c "printf '%s' '$payload' | '$REPO_ROOT/ai/bin/record-filed-issue'"
  [ "$status" -eq 0 ]
  [ "$(_rfi_entry_count)" = "0" ]
}

# ── session-lock ─────────────────────────────────────────────────────────────
# Same split as tree-lock-guard: the claim is in detect.ts, which imports one
# node builtin, so node can load it directly. index.ts is the wiring no test
# can reach.
#
# The record these write is read by ai/lib/fix/engine.py to refuse a pass, and
# by ai/claude/bin/claude-session-lock for the other harness. Tests record a
# real live pid rather than stubbing the CLI, so a renamed binary or a moved
# lock path fails here rather than silently never claiming.

# _session_claim REPO PID MODE — acquire or release through detect.ts.
_session_claim() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/pi/extensions/session-lock/detect.ts');
    const pid = Number(process.argv[2]);
    const out = process.argv[3] === 'release'
      ? m.releaseFor(process.argv[1], pid)
      : m.acquireFor(process.argv[1], pid, 'sess-1');
    process.stdout.write(String(out));
  " -- "$1" "$2" "${3:-acquire}"
}

@test "session-lock: a claim makes the worktree read as being edited" {
  local repo="$TMPDIR/sl-repo"
  mkdir -p "$repo"
  git -C "$repo" init -q

  # A process that outlives the node run: the record is only live while its
  # pid is, so claiming node's own pid would prune itself on the next read.
  sleep 30 &
  local holder=$!

  _session_claim "$repo" "$holder"
  [ "$output" = "true" ]

  run "$REPO_ROOT/bin/local/with-session-lock" "$repo" --check
  [ "$status" -eq 0 ]
  [[ "$output" == *"pid $holder"* ]]

  kill "$holder" 2>/dev/null || true
}

@test "session-lock: releasing clears the claim" {
  local repo="$TMPDIR/sl-release"
  mkdir -p "$repo"
  git -C "$repo" init -q

  sleep 30 &
  local holder=$!

  _session_claim "$repo" "$holder"
  _session_claim "$repo" "$holder" release
  [ "$output" = "true" ]

  run "$REPO_ROOT/bin/local/with-session-lock" "$repo" --check
  [ "$status" -eq 1 ]

  kill "$holder" 2>/dev/null || true
}

@test "session-lock: a killed session stops holding the worktree" {
  # Kill-safety without a daemon, which is the whole reason the claim is a
  # record rather than a flock. `wait` is the synchronisation point.
  local repo="$TMPDIR/sl-killed"
  mkdir -p "$repo"
  git -C "$repo" init -q

  sleep 30 &
  local holder=$!
  _session_claim "$repo" "$holder"

  kill -9 "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true

  run "$REPO_ROOT/bin/local/with-session-lock" "$repo" --check
  [ "$status" -eq 1 ]
}

@test "session-lock: detect.ts imports no SDK" {
  run grep -E '@earendil-works/pi-coding-agent|isToolCallEventType' \
    "$REPO_ROOT/ai/pi/extensions/session-lock/detect.ts"
  [ "$status" -ne 0 ]
}

# ─── superpowers-bootstrap ────────────────────────────────────────────────
# Delivers the superpowers bootstrap as a system-prompt section. bootstrap.ts
# imports only node built-ins, so node loads it directly.

# _section SKILLS_JSON — prints bootstrapSection(SKILLS) as JSON (null or text).
_section() {
  run node --input-type=module -e "
    const { bootstrapSection } = await import('$REPO_ROOT/ai/pi/extensions/superpowers-bootstrap/bootstrap.ts');
    process.stdout.write(JSON.stringify(bootstrapSection(JSON.parse(process.argv[1]))));
  " -- "$1"
}

@test "superpowers-bootstrap: the loaded skill's body becomes the section" {
  mkdir -p "$TMPDIR/sp"
  printf -- '---\nname: using-superpowers\ndescription: x\n---\n\nUse skills first.\n' > "$TMPDIR/sp/SKILL.md"

  _section "[{\"name\":\"other\",\"filePath\":\"/nope\"},{\"name\":\"using-superpowers\",\"filePath\":\"$TMPDIR/sp/SKILL.md\"}]"
  [ "$status" -eq 0 ]
  [[ "$output" == *'Use skills first.'* ]]
  [[ "$output" != *'description: x'* ]]
}

@test "superpowers-bootstrap: the section carries Pi's subagent and task-list mapping" {
  mkdir -p "$TMPDIR/sp"
  printf -- '---\nname: using-superpowers\n---\nBody.\n' > "$TMPDIR/sp/SKILL.md"

  _section "[{\"name\":\"using-superpowers\",\"filePath\":\"$TMPDIR/sp/SKILL.md\"}]"
  [ "$status" -eq 0 ]
  [[ "$output" == *'subagent tool'* ]]
  [[ "$output" == *'task-list tool'* ]]
}

@test "superpowers-bootstrap: the section names the skill directory relative paths resolve against" {
  # The skill links references/pi-tools.md by relative path; read from the
  # system prompt it has no directory to resolve that against unless told.
  mkdir -p "$TMPDIR/sp"
  printf -- '---\nname: using-superpowers\n---\nSee references/pi-tools.md.\n' > "$TMPDIR/sp/SKILL.md"

  _section "[{\"name\":\"using-superpowers\",\"filePath\":\"$TMPDIR/sp/SKILL.md\"}]"
  [ "$status" -eq 0 ]
  [[ "$output" == *"resolve against $TMPDIR/sp."* ]]
}

@test "superpowers-bootstrap: frontmatter closed at end of file without a newline is stripped" {
  run node --input-type=module -e "
    const { stripFrontmatter } = await import('$REPO_ROOT/ai/pi/extensions/superpowers-bootstrap/bootstrap.ts');
    process.stdout.write(JSON.stringify(stripFrontmatter('---\nname: x\n---')));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '""' ]
}

@test "superpowers-bootstrap: no skills loaded means no section" {
  # The review pipeline runs Pi with --no-skills; its parsed-JSON answers must
  # not be told to announce a skill first.
  _section '[]'
  [ "$status" -eq 0 ]
  [ "$output" = null ]
}

@test "superpowers-bootstrap: an unreadable skill file adds nothing rather than failing" {
  _section '[{"name":"using-superpowers","filePath":"/nonexistent/SKILL.md"}]'
  [ "$status" -eq 0 ]
  [ "$output" = null ]
}

@test "superpowers-bootstrap: the section name is one Pi accepts" {
  run node --input-type=module -e "
    const { SECTION_NAME } = await import('$REPO_ROOT/ai/pi/extensions/superpowers-bootstrap/bootstrap.ts');
    process.stdout.write(String(/^[a-z][a-z0-9_-]*\$/.test(SECTION_NAME)));
  "
  [ "$output" = true ]
}

@test "superpowers-bootstrap: never edits the transcript" {
  # The whole fix: a context hook that changes messages is what broke signed
  # thinking blocks. The bootstrap belongs in the system prompt only.
  run grep -rqE 'pi\.on\("context' "$REPO_ROOT/ai/pi/extensions/superpowers-bootstrap"
  [ "$status" -ne 0 ]
  run grep -q 'pi.on("before_agent_start"' "$REPO_ROOT/ai/pi/extensions/superpowers-bootstrap/index.ts"
  [ "$status" -eq 0 ]
}
