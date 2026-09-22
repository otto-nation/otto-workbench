import { statements } from "../_shared/statements.ts";

/**
 * The masked-exit-status predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file pulls in nothing but ../_shared, which imports nothing itself, so
 * tests/pi_extensions.bats can run it under plain `node` and assert the shapes
 * it does and does not match.
 *
 * This is the sibling of test-pipe-guard, and the same rule wearing a different
 * costume. There, a filter downstream of a suite discards the suite's status.
 * Here, a *trailing statement* does: a command ending `pytest …; echo "EXIT=$?"`
 * exits with echo's status, which is 0 whatever the suite did.
 *
 * The distinction that makes this worth its own guard is where the damage lands.
 * In a foreground bash call the masked status is cosmetic — the agent reads the
 * `EXIT=1` line and learns the truth. Handed to `job_start` it is not: the job
 * facility reports the *process* status, so the completion notice says
 * "succeeded" for a suite that failed, and the true status is only in a line of
 * output nobody re-reads. Three false "succeeded" notices in one session are
 * what this exists to prevent, and all three were job_start calls.
 *
 * There is no counterpart in ai/claude/bin/claude-bash-guard, because the
 * failure needs a job facility that surfaces a process exit code as a
 * pass/fail notice, and Claude Code's background bash does not report one that
 * way. TEST_RUNNERS is still imported from test-pipe-guard rather than copied,
 * so the two Pi guards cannot drift apart about what counts as a test run —
 * and through that list, cannot drift from Claude's either.
 */

/**
 * A statement that reports a status instead of producing one.
 *
 * `echo $?`, `printf "EXIT=%d" $?`, and the `echo "EXIT=$?"` spelling that
 * caused this. The `$?` is required: a trailing `echo done` masks a status too,
 * but it reads as a progress line rather than a status report, and blocking it
 * would make this guard noise. What is caught is the shape that *claims* to
 * report the status while discarding it.
 */
const REPORTS_STATUS = /^\s*(echo|printf)\b[^|]*\$\?/;

/**
 * A statement that writes the status somewhere durable rather than to stdout.
 *
 * `echo $? > .exit` and `echo $? >> out.txt` are the honest form of the same
 * line — the status is being persisted for something else to read, which is a
 * real pattern and not a claim that the run passed. It still masks the
 * process's own status, so it is only exempt when the caller is a foreground
 * bash call; isMaskedExitStatus takes `reportsToCaller` for that.
 */
const REDIRECTS = /[0-9]*>>?\s*\S/;

/** The last path segment of a token, so `bin/local/run-tests` reads as `run-tests`. */
function basename(token: string): string {
  const cleaned = token.replace(/^['"]|['"]$/g, "");
  return cleaned.slice(cleaned.lastIndexOf("/") + 1);
}

/**
 * Whether a statement invokes one of `runners`.
 *
 * Deliberately simpler than test-pipe-guard's `invokesRunner`: that one has to
 * exempt `npm run build`, because piping a build's output is ordinary. Here the
 * question is only whether a status worth keeping was produced, and a masked
 * `npm run build` in a background job is the same false green as a masked
 * `npm test`. Leading environment assignments are skipped so `CI=1 npm test`
 * still matches.
 */
function invokesRunner(statement: string, runners: readonly string[]): boolean {
  const tokens = statement.trim().split(/\s+/).filter(Boolean);
  for (const token of tokens) {
    if (token.includes("=")) continue;
    return runners.includes(basename(token));
  }
  return false;
}

/**
 * Whether a tool's caller reads the printed status rather than a process code.
 *
 * The whole reason this guard is not just a line in test-pipe-guard, so it is
 * kept here with the predicate rather than inline in index.ts, where the SDK
 * import puts it beyond the reach of the test suite. Getting this mapping
 * backwards would leave the job_start case — the one that actually produced
 * false green — exempt whenever the command redirects, which is nearly always.
 *
 * `bash` is foreground: the agent reads the `EXIT=1` line and learns the truth,
 * so a redirect that persists the status is honest. `job_start` reports the
 * *process's* exit code as a pass/fail notice, so a masked status is announced
 * as success and no amount of writing it elsewhere redeems that.
 */
export function readsPrintedStatus(tool: string): boolean {
  return tool === "bash";
}

/**
 * The runner whose status a trailing report is about to discard, or null.
 *
 * `reportsToCaller` is true for a foreground bash call, where the agent reads
 * the printed status and a redirect to a file is a reasonable thing to do. It
 * is false for `job_start`, where the harness reads the process's exit code and
 * a masked one is reported to the user as success — so there, persisting the
 * status elsewhere does not redeem the command.
 */
export function maskedRunner(
  command: string,
  runners: readonly string[],
  reportsToCaller: boolean,
): string | null {
  const parts = statements(command).filter((s) => s.trim() !== "");
  if (parts.length < 2) return null;

  const last = parts[parts.length - 1];
  if (!REPORTS_STATUS.test(last)) return null;
  if (reportsToCaller && REDIRECTS.test(last)) return null;

  for (const statement of parts.slice(0, -1)) {
    if (!invokesRunner(statement, runners)) continue;
    const named = statement.trim().split(/\s+/).find((t) => !t.includes("="));
    if (named !== undefined) return basename(named);
  }
  return null;
}
