/**
 * The `sleep`-as-a-wait predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file imports nothing, which is what lets tests/pi_extensions.bats run it under
 * plain `node` and assert the shapes it does and does not match.
 */

/**
 * Where a sleep stops being a pipeline step and starts being a wait.
 *
 * A second or two letting a server bind its port before the first request is a
 * real step, and blocking it would make this guard's refusals noise. Nothing at
 * ten seconds and up is that: it is a wait for something that will announce
 * itself. Kept in step with SLEEP_WAIT_THRESHOLD_SECONDS in
 * ai/claude/bin/claude-bash-guard — tests/pi_extensions.bats holds the two to one
 * value, so changing this one alone fails the suite rather than letting the
 * harnesses drift.
 */
export const THRESHOLD_SECONDS = 10;

/**
 * A bare `sleep <n>` at the head of a statement.
 *
 * Anchoring to a statement boundary is what keeps `some-cmd --sleep 5` and a
 * `grep sleep` over a script out of it — the word has to be the command being
 * run. A fractional duration is under the threshold by definition and is not
 * matched at all.
 *
 * The `m` flag is load-bearing: a newline separates two statements as surely as
 * a semicolon does, and without it `^` matches only the start of the whole
 * command — so a `sleep 300` on the second line would pass. Claude's guard
 * reaches the same place by joining the lines it scans with `; ` before applying
 * its own anchor.
 *
 * ceiling: the duration is read as a plain integer, so a suffixed `sleep 5m` — a
 * GNU and BSD extension, not POSIX — passes. Upgrade to parsing the suffix if one
 * shows up; `sleep <seconds>` is the shape an agent writes.
 */
const SLEEP_STATEMENT = /(?:^|[;&|]\s*)sleep\s+(\d+)(?:\s|;|$)/m;

/** The sleep duration in a command, or null when it holds no bare `sleep <n>`. */
export function sleepSeconds(command: string): number | null {
  const match = SLEEP_STATEMENT.exec(command);
  return match ? Number(match[1]) : null;
}

/** Whether `command` sleeps long enough to be a wait rather than a settle. */
export function isWaitingSleep(command: string): boolean {
  const seconds = sleepSeconds(command);
  return seconds !== null && seconds >= THRESHOLD_SECONDS;
}
