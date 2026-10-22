import { spawnSync, type SpawnSyncReturns } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { statements } from "./statements.ts";

/**
 * Which bash commands write to GitHub, as the Pi guards ask it.
 *
 * Two halves. `mightWriteGithub` is a cheap superset test run in process on
 * every command: false means no statement mentions a program that can reach
 * GitHub, and the command is not a write. True means only "ask". The verdict
 * is `owners-check --classify` (ai/lib/gh/write_shapes.py), the one classifier
 * all three harness paths share — it peels wrappers (`sudo`, `env -S`,
 * `xargs`, `sh -c` …) and that took four security review rounds to get right
 * in one language. A copy here would be a second implementation of a security
 * boundary, free to drift; the superset test can only cost a Python start-up,
 * never a missed write.
 *
 * `ai/claude/bin/claude-bash-guard` (`_command_might_write_github`) keeps the
 * same pre-filter over the same names. tests/github_write_shapes.bats drives
 * tests/fixtures/github-write-shapes.tsv through both harnesses, so a write
 * the pre-filter misses fails there as a write that was allowed.
 *
 * Imports only Node built-ins and ./statements.ts, so a guard's detect.ts can
 * re-export it and tests can load it under plain `node`.
 *
 * ceiling: a program reached through a variable (`$GH issue create`), an
 * alias, or a wrapper script of another name is never seen, by this pre-filter
 * or by the classifier. Upgrade trigger: the first such spelling seen in an
 * agent transcript — add it to the corpus, then to all three.
 */

/**
 * Program names that can write to GitHub: `gh` and the workbench commands that
 * post. A deliberate superset of `_GITHUB_COMMANDS` in
 * ai/lib/gh/write_shapes.py, never a copy of it: an extra name costs one
 * classify call and cannot let a write through, while a missing one would hide
 * every write Python knows it for. `review-orchestrate` is the extra — it runs
 * `review-post`, so the pre-filter already asks about it should the classifier
 * learn it. Same list as `GITHUB_WRITE_PROGRAMS` in
 * ai/claude/bin/claude-bash-guard; tests/github_write_shapes.bats fails when
 * either loses a name Python has.
 */
export const GITHUB_WRITE_PROGRAMS: ReadonlySet<string> = new Set([
  "gh",
  "pr",
  "pr-describe",
  "review",
  "review-post",
  "review-threads",
  "review-orchestrate",
]);

/** ai/bin/owners-check, from this module's real path in the checkout. */
const CHECKOUT_OWNERS_CHECK = fileURLToPath(new URL("../../../bin/owners-check", import.meta.url));

/**
 * The owners-check to run: BESIDE_CHECKOUT when it exists, else the bare name,
 * which spawnSync looks up on PATH — where a layout that ships the extensions
 * apart from ai/bin keeps it. Not found on PATH either is a spawn error, which
 * classifiesAsWrite counts as a write.
 */
export function locateOwnersCheck(besideCheckout: string = CHECKOUT_OWNERS_CHECK): string {
  return existsSync(besideCheckout) ? besideCheckout : "owners-check";
}

/** The owners-check the guards run by default. */
export const OWNERS_CHECK = locateOwnersCheck();

/**
 * How long any owners-check call — the classify call here, upstream-guard's
 * owner-boundary check — may take before it counts as a check that could not
 * run. claude-bash-guard's OWNERS_CHECK_TIMEOUT_SECONDS is the same bound in
 * seconds; tests/pi_extensions_tokenize.bats holds the two to one value.
 */
export const CLASSIFY_TIMEOUT_MS = 15_000;

/** owners-check --classify's stdout for a command that does not write (with exit 0). */
const NOT_A_WRITE_ANSWER = "read";

/** Characters a token is made of; everything else separates tokens. */
const SEPARATOR = /[^A-Za-z0-9_.-]+/;

/** True when TOKEN is a program name, or an option cluster ending in one (`-Sgh`). */
function tokenNamesProgram(token: string): boolean {
  if (GITHUB_WRITE_PROGRAMS.has(token)) return true;
  if (!token.startsWith("-") || token.startsWith("--")) return false;
  for (let at = 2; at < token.length; at++) {
    if (GITHUB_WRITE_PROGRAMS.has(token.slice(at))) return true;
  }
  return false;
}

/**
 * True when STATEMENT mentions a program that can write to GitHub anywhere in
 * it, as a word, a path's last part, an `--opt=` value or inside quotes.
 *
 * Read twice — quotes and backslashes deleted (`"g"h`, `g\h` are `gh` to the
 * shell) and quotes as separators (`-S'gh …'`) — because the classifier reads
 * it both ways too, and a superset has to cover both.
 */
function statementMentionsGithub(statement: string): boolean {
  const text = `${statement.replace(/[\\'"]/g, "")} ${statement.replace(/['"]/g, " ")}`;
  return text.split(SEPARATOR).some(tokenNamesProgram);
}

/** True when COMMAND might write to GitHub — the superset; heredoc bodies are content and never match. */
export function mightWriteGithub(command: string): boolean {
  return statements(command).some(statementMentionsGithub);
}

/**
 * Run OWNERS_CHECK with ARGS for at most TIMEOUT_MS, stdout and stderr captured
 * as text. The one runner every owners-check call goes through, so the bound and
 * the spawn options cannot differ between callers. A spawn failure or timeout
 * is on the result's `error` (`status` is then null), never thrown.
 */
export function runOwnersCheck(
  args: readonly string[],
  ownersCheck: string = OWNERS_CHECK,
  timeoutMs: number = CLASSIFY_TIMEOUT_MS,
): SpawnSyncReturns<string> {
  return spawnSync(ownersCheck, args, {
    stdio: ["ignore", "pipe", "pipe"],
    encoding: "utf8",
    timeout: timeoutMs,
  });
}

/**
 * True unless owners-check --classify answers that COMMAND does not write:
 * exit 0 with exactly `read` on stdout.
 *
 * Fails closed: a missing checker, a missing python3, a crash (an uncaught
 * exception also exits 1), unexpected output or a timeout all count as a
 * write, because the callers refuse a write and a guard that allowed on its
 * own inability to answer would be no guard.
 */
export function classifiesAsWrite(
  command: string,
  ownersCheck: string = OWNERS_CHECK,
  timeoutMs: number = CLASSIFY_TIMEOUT_MS,
): boolean {
  const run = runOwnersCheck(["--classify", "--", command], ownersCheck, timeoutMs);
  return !(run.status === 0 && typeof run.stdout === "string" && run.stdout.trim() === NOT_A_WRITE_ANSWER);
}

/** True when COMMAND writes to GitHub: the pre-filter, then the classifier. */
export function isGithubWrite(command: string, ownersCheck: string = OWNERS_CHECK): boolean {
  return mightWriteGithub(command) && classifiesAsWrite(command, ownersCheck);
}
