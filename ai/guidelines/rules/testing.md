# Testing

## Writing Tests

- Write tests the same way as existing tests in the project
- Tests are not complete until they run and all pass
- Never disable a test as a fix for a failing test
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
- Enforced by `ai/pi/extensions/test-pipe-guard` under Pi and by
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

`bin/local/validate-new-tests` does this from the diff — it runs the tests a
change adds against a worktree at the merge base and reports the ones that pass
there. Treat its two findings differently, because they are not equally strong.
A test that merely passes at base is usually fine: a negative or back-compat
case passes without the change by design, and on this repo's own history that
signal alone is right about one time in eight. A test that passes at base *and*
asserts something absent its own fixture never creates is the shape that cannot
fail, and that pair is what the check fails on. Neither verdict replaces the
revert above for a test you have reason to doubt — a test can fail at base for a
reason other than the one it was written for, and no runner can see the
difference.

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
