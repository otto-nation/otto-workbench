#!/usr/bin/env bats
# Tests for the Pi review-guard.
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
