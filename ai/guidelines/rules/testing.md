# Testing

## Writing Tests

- Write tests the same way as existing tests in the project
- Tests are not complete until they run and all pass
- Never disable a test as a fix for a failing test
- A test failing because it reached for a tool the CI platform lacks is a portability bug in the test, not a case that needs a platform guard. Read the subject portably instead: every CI job here runs `ubuntu-24.04`, so `if [[ "$OSTYPE" != "darwin"* ]]; then skip; fi` over an assertion about a *tracked file* is an assertion that exists and never executes, on any runner. A case whose subject genuinely needs one OS — BSD `ln -sfh` semantics, launchd actually loading a plist — is the legitimate form, and says so with `# platform-only: <why the subject needs this OS>` above the `@test`. Enforced by `bin/local/validate-skip-coverage`, which reads the test body rather than the guard: the two spellings of the skip line are identical, so only the subject tells them apart
- When a foundational method's contract changes, audit every test that asserts the old behavior and update it
- Prefer real dependencies over mocks when feasible — mocks hide integration bugs
- Every bug fix and behavioral change must include a regression test

## Reading a Suite Result

- Never pipe a test suite or validator into `tail`, `head`, `grep`, or any other
  filter to inspect its result. The pipeline's exit status is the filter's, not the
  suite's — `false | tail -1` exits 0 — so a failing run reads as a pass, and a
  `$?` captured after the pipe is reporting on the filter. Redirect to a file and
  read the status, then grep the file
- `set -o pipefail` makes the pipeline report the first failing stage, which is the
  one narrow way a pipe is safe here. Prefer the redirect anyway: the status is the
  thing being checked, and a file leaves the whole run to read afterwards
- Do not finish the command with `; echo $?` either. A trailing statement becomes
  the command's own exit status, so `npm test > out.txt 2>&1; echo "EXIT=$?"` exits
  0 whatever the suite did — the same masking as the pipe, one statement later. The
  printed line is truthful and a foreground caller reads it, which is what makes the
  habit feel safe
- It stops being cosmetic the moment the command is handed to a background job. The
  job facility reports the *process's* exit code as its pass/fail notice, so a
  masked status is announced as "succeeded" for a suite that failed, and the real
  result is in output nobody re-reads. Three such notices in one session are why
  this is written down. Let the runner be the last thing the command does and read
  the output afterwards; where a status must be captured mid-command, end with an
  honest `exit "$status"`
- Enforced by `ai/pi/extensions/test-pipe-guard` and
  `ai/pi/extensions/exit-status-guard` under Pi, and by
  `ai/claude/bin/claude-bash-guard` under Claude Code

## A Test Must Fail When Its Subject Breaks

A test that cannot fail is worse than no test: it reports the behavior is held
when nothing holds it. Before believing a passing test, check all three.

The check that settles it is to break the subject and watch the test fail. Delete
the line the test exists to hold, run it, confirm it fails, restore. A passing
test proves the code passes; only a failing one proves the test is holding
anything. Every item below is a way a test can look right and hold nothing, and
the revert catches all of them at once — including the ways not listed.

Do this hardest on a test written to close a review finding. The recurring
failure is not a wrong fix but a correct fix with a test that does not constrain
it: a fixture that stubs out the very function the fix changed, an assertion on
what the caller passed rather than on what the callee did with it. The fix is
right, the test passes, and deleting the fix leaves it passing.

A filter is the other way to fool yourself here: `-k`, a `grep`, a subset path.
A revert whose test was deselected reports no failures and reads as proof. Run
the whole file.

Revert the line that was actually wrong, which is not always the line the test
names. A helper can be correct and its caller defeat it: a cache read that a
caller already invalidated, a flag the caller never passes, a guard the caller
skips. Reverting the helper fails the helper's test and proves nothing about
the wiring, so the bug ships under a green suite. When a change adds a function
*and* a call to it, the revert that settles it is at the call site — delete the
argument, restore the old call, put the wrong order back — and a test that
survives that is testing the unit, not the behaviour.

A test whose subject is a periodic or conditional path must pin the condition
that selects it. Shared counters, clocks and module state decide which branch a
test exercises, and the default is rarely the one it is named for: a tick
counter fresh at `0` takes the every-Nth-run branch, so a test written for the
ordinary path silently drove the rare one and passed on a fixture that returned
the same value either way. Set the selector explicitly, and confirm from the
code's own output — a log line, a spy, a counter — that the branch you meant is
the branch that ran. A seam that can only reset to the default cannot express
this: make it take the value.

A test that fails only because the symbol is missing has not been checked yet.
Against a base without the change, `is not a function` and a real assertion
failure are the same red, and only one of them means the test constrains
anything. Get the new code in place, then break its *behaviour* — pin the
return, neuter the branch, leave the signature alone — and watch the assertion
fail on the value. This is the case `check-new-tests` says it cannot see.

`bin/local/check-new-tests` does this from the diff — it runs the tests a
change adds against a worktree at the merge base and reports the ones that pass
there. Treat its two findings differently, because they are not equally strong.
A test that merely passes at base is usually fine: a negative or back-compat
case passes without the change by design, and on this repo's own history that
signal alone is right about one time in eight. A test that passes at base *and*
asserts the absence of something its own fixture never creates is the shape
that cannot fail, and that pair is what the check fails on. Neither verdict
replaces the revert above for a test you have reason to doubt — a test can
fail at base for a reason other than the one it was written for, and no runner
can see the difference.

A test that legitimately passes at base takes a marker, in the grammar
`ceiling:` uses, immediately above the test:

```bash
# passes-at-base: asserts behaviour this change was careful not to break
@test "a second call replaces the map rather than merging into it" {
```

A marker with no reason after the colon declares nothing and does not suppress
the finding, for the same reason a bare `ceiling:` does not satisfy its gate.

- Do not add tests that simply assert constant values
- The assertion cannot be satisfied incidentally. An `or` arm that is always true (`assert x in content or " " in text`) makes the whole assertion a tautology, and it passes on any input
- The patch target is the name the code under test actually looks up. Patching `mod.subprocess.run` proves nothing once the function was migrated to call `gh_client.api` — the test keeps passing against code it no longer touches
- The input reaches the branch under test rather than stopping at an early guard. A test named for a commit failure that supplies input rejected before the commit is attempted exercises the guard, not the failure
- Cover the path the change is actually on before the edge case beside it. A commit that reworks a common-path helper and adds a case only for non-ASCII input, an empty list, or a rare error has tested the thing it did not change; the common path it did change is still held by nothing
- Assert the exit status alongside the output. A test matching only stdout passes for a command that emitted the expected line and then failed
- Cover both unset and empty-string when testing a config or env-var fallback — real environments export `VAR=""`, and `get(k, default)` returns the empty string where `get(k) or default` returns the default

## Test Isolation

- Keep test files inside the test's own `tmp_path` — a static filename in `tmp_path.parent` or `/tmp` is a race between tests sharing that directory
- A temp git repo in a fixture needs an identity (`-c user.name=... -c user.email=...` or `GIT_AUTHOR_*`), or its commits fail on any machine without a global git config
- A bats suite gets its `$TMPDIR` pin from `common_setup()` in `tests/test_helper.bash`, which every suite already calls — do not repeat the line in `setup()`. Override it only for a form the helper cannot give you, such as the `pwd -P` spelling two suites need because macOS reports `$BATS_TEST_TMPDIR` under `/var` while a canonicalising tool reports `/private/var`. Never `mktemp -d`: bats makes and removes its own scratch for every case, passing or failing, so a hand-rolled directory is one nobody collects
- Never write `rm -rf "$TMPDIR"`. bats runs `teardown()` even when `setup()` failed partway, so a setup that dies before its pin line — a failed `load`, an undefined helper, a sourced lib mid-edit — arrives in teardown with `TMPDIR` still holding the machine's real temp directory and removes all of it. Removing a path *under* `$TMPDIR` is fine; it cannot escape the pin. Enforced by `bin/local/validate-tmpdir-isolation`
- The tree lock is per-worktree but `$TMPDIR` is per-machine, so two worktrees running the suite at once would otherwise take sibling scratch roots under one parent and a sweep of that parent takes the other run's live directory. `bin/local/run-tests` gives each worktree its own `BATS_RUN_TMPDIR` for this reason — keep concurrent suite runs going through it rather than invoking `bats` directly across worktrees. The symptom when it goes wrong is not a failed assertion: the losing run reports `teardown_file failed` for tests that never ran, with a total short of the suite's real count
