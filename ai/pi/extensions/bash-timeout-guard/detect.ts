/**
 * The bash-timeout predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded inside a Pi session. This file imports only ../_shared, which
 * imports nothing, so tests/pi_extensions_bash_timeout.bats can run it under
 * plain `node`.
 *
 * Pi-only, with no twin in ai/claude/bin/claude-bash-guard, because both facts
 * it rests on are Pi's: the bash tool's `timeout` is in seconds (Claude Code's
 * is in milliseconds), and when it fires Pi SIGKILLs the command's whole
 * process group with no SIGTERM first (`killProcessTree`). A git command
 * killed that way mid-write leaves its `*.lock` behind, and every later git
 * write in the repo then fails until someone deletes it by hand.
 *
 * Measured across 905 Pi sessions: 43% of bash calls passed `timeout`, about
 * half of those in milliseconds — `15000`, a four-hour bound — and 103 of the
 * 115 calls killed by their own timeout were running git, mostly under a 10–30
 * second bound that ai/guidelines/rules/general.md had told the agent to pass.
 */

import { splitStatements } from "../_shared/statements.ts";
import { tokenize } from "../_shared/tokenize.ts";

/**
 * The longest bound the bash tool should be given, in seconds.
 *
 * Every millisecond value seen in practice (5000 and up) is above it, so this
 * one number catches the unit mistake; and a foreground call that may run past
 * an hour belongs in `job_start` anyway, which reports its own completion.
 */
export const MAX_SECONDS = 3600;

/**
 * The shortest tool bound allowed on a command that writes git state or runs a
 * suite, in seconds. Below it the bound is a guess at how long the work takes,
 * and a guess that comes in short is a SIGKILL mid-write.
 */
export const MIN_SECONDS_FOR_WRITERS = 300;

/**
 * The commands that write git state or run a suite, matched on the last path
 * segment of the program a statement runs. `git` read-only subcommands are in
 * because `status` and `diff` refresh the index and take `index.lock`.
 */
export const WRITERS = [
  "git",
  "pr",
  "wt",
  "review",
  "pytest",
  "bats",
  "run-tests",
  "validate-all",
];

/** The last path segment of a token, so `ai/bin/pr` reads as `pr`. */
function basename(token: string): string {
  return token.slice(token.lastIndexOf("/") + 1);
}

/**
 * The program a statement runs, as written: leading `VAR=value` assignments
 * are skipped, so `GIT_TRACE=1 git fetch` runs `git`. "" for an empty statement.
 */
function program(statement: string): string {
  const words = tokenize(statement).filter((t) => !t.operator);
  const head = words.find((t) => t.quoted || !/^[A-Za-z_][A-Za-z0-9_]*=/.test(t.value));
  return head === undefined ? "" : head.value;
}

const UNIT_SECONDS: Record<string, number> = { "": 1, s: 1, m: 60, h: 3600, d: 86400 };

/**
 * The duration a coreutils `timeout` statement gives its command, in seconds,
 * or null when it is not one or the duration does not parse. Its own flags
 * (`-s KILL`, `--kill-after=5`, `-k 5`) are skipped to reach the duration.
 */
function wrapperSeconds(statement: string): number | null {
  const words = tokenize(statement).filter((t) => !t.operator).map((t) => t.value);
  const at = words.findIndex((w) => !/^[A-Za-z_][A-Za-z0-9_]*=/.test(w));
  if (at < 0 || basename(words[at]) !== "timeout") return null;
  for (let i = at + 1; i < words.length; i++) {
    const word = words[i];
    if (word === "-s" || word === "-k" || word === "--signal" || word === "--kill-after") {
      i++;
      continue;
    }
    if (word.startsWith("-")) continue;
    const m = /^(\d+(?:\.\d+)?)([smhd]?)$/.exec(word);
    return m === null ? null : Number(m[1]) * UNIT_SECONDS[m[2]];
  }
  return null;
}

/**
 * The writer a statement runs that the tool's *timeout* could SIGKILL, or "".
 *
 * A statement led by coreutils `timeout` is bounded the gentle way — SIGTERM
 * first, so git cleans up its lock — but only when it fires first: a wrapper
 * longer than the tool's own bound is still cut off by the tool's SIGKILL. A
 * `bin/local/` script counts as a writer: those are the repo's validators and
 * test runners.
 */
function unboundedWriter(statement: string, timeout: number): string {
  const wrapper = wrapperSeconds(statement);
  if (wrapper !== null && wrapper < timeout) return "";
  const prog = wrapper === null ? program(statement) : wrappedProgram(statement);
  if (prog === "") return "";
  if (prog.includes("bin/local/")) return prog;
  return WRITERS.includes(basename(prog)) ? basename(prog) : "";
}

/** The command a coreutils `timeout` statement runs: the first word after its duration. */
function wrappedProgram(statement: string): string {
  const words = tokenize(statement).filter((t) => !t.operator).map((t) => t.value);
  const at = words.findIndex((w) => /^\d+(?:\.\d+)?[smhd]?$/.test(w));
  return at >= 0 && at + 1 < words.length ? words[at + 1] : "";
}

/**
 * Why a bash call's `timeout` is refused. A const object rather than an
 * `enum`: node's type stripping, which the bats suite loads this under, does
 * not support enums.
 */
export const TimeoutProblem = {
  // Over MAX_SECONDS: almost always milliseconds passed as seconds.
  TooLong: "too_long",
  // Under MIN_SECONDS_FOR_WRITERS on a command that writes git state or runs a suite.
  ShortOnWriter: "short_on_writer",
} as const;

export type TimeoutProblem = (typeof TimeoutProblem)[keyof typeof TimeoutProblem];

export interface TimeoutFinding {
  problem: TimeoutProblem;
  // The writer the command runs, for ShortOnWriter; "" otherwise.
  writer: string;
}

/**
 * What is wrong with running *command* under a bash tool `timeout` of
 * *timeout* seconds, or null when nothing is. No timeout is never a finding.
 */
export function timeoutFinding(command: string, timeout: number | undefined): TimeoutFinding | null {
  if (timeout === undefined || timeout === null) return null;
  if (timeout > MAX_SECONDS) return { problem: TimeoutProblem.TooLong, writer: "" };
  if (timeout >= MIN_SECONDS_FOR_WRITERS) return null;
  for (const statement of splitStatements(command)) {
    const writer = unboundedWriter(statement.text, timeout);
    if (writer !== "") return { problem: TimeoutProblem.ShortOnWriter, writer };
  }
  return null;
}
