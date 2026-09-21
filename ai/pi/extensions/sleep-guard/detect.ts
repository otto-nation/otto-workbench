import { statements } from "../_shared/statements.ts";

/**
 * The `sleep`-as-a-wait predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file pulls in nothing but ../_shared, which imports nothing itself, so
 * tests/pi_extensions.bats can run it under plain `node` and assert the shapes
 * it does and does not match.
 *
 * What counts as a sleep here is meant to match ai/claude/bin/claude-bash-guard
 * decision for decision: same threshold, same statement anchoring, same heredoc
 * exemption — the splitting behind the last two is ../_shared/statements.ts,
 * shared with the other guards for the same reason. Two guards enforcing one rule that disagree about a given command
 * are worse than one guard, because which answer you get depends on which
 * harness you happen to be in.
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
 * Applied per statement rather than to the whole command, so the anchor is a
 * plain `^`. That is what keeps `some-cmd --sleep 5` and a `grep sleep` over a
 * script out of it — the word has to be the command being run. A fractional
 * duration is under the threshold by definition and is not matched at all.
 *
 * ceiling: the duration is read as a plain integer, so a suffixed `sleep 5m` — a
 * GNU and BSD extension, not POSIX — passes. Upgrade to parsing the suffix if one
 * shows up; `sleep <seconds>` is the shape an agent writes.
 */
const SLEEP_STATEMENT = /^\s*sleep\s+(\d+)(?:\s|$)/;

/**
 * The longest bare `sleep <n>` in a command, or null when it holds none.
 *
 * The longest rather than the first: a command's statements are all going to run,
 * so the one that decides whether this is a wait is the largest of them.
 */
export function sleepSeconds(command: string): number | null {
  let longest: number | null = null;
  for (const statement of statements(command)) {
    const match = SLEEP_STATEMENT.exec(statement);
    if (!match) continue;
    const seconds = Number(match[1]);
    if (longest === null || seconds > longest) longest = seconds;
  }
  return longest;
}

/** Whether `command` sleeps long enough to be a wait rather than a settle. */
export function isWaitingSleep(command: string): boolean {
  const seconds = sleepSeconds(command);
  return seconds !== null && seconds >= THRESHOLD_SECONDS;
}
