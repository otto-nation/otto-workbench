import { spawnSync } from "node:child_process";
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
 * post. Kept in step with `_GITHUB_COMMANDS` in ai/lib/gh/write_shapes.py and
 * `GITHUB_WRITE_PROGRAMS` in ai/claude/bin/claude-bash-guard.
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
export const OWNERS_CHECK = fileURLToPath(new URL("../../../bin/owners-check", import.meta.url));

/** How long a classify call may take before it counts as a check that could not run. */
export const CLASSIFY_TIMEOUT_MS = 15_000;

/** owners-check --classify's answer for a command that does not write. */
const NOT_A_WRITE_STATUS = 1;

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
 * True unless owners-check --classify answers that COMMAND does not write.
 *
 * Fails closed: a missing checker, a missing python3, a crash or a timeout
 * all count as a write, because the callers refuse a write and a guard that
 * allowed on its own inability to answer would be no guard.
 */
export function classifiesAsWrite(
  command: string,
  ownersCheck: string = OWNERS_CHECK,
  timeoutMs: number = CLASSIFY_TIMEOUT_MS,
): boolean {
  const run = spawnSync(ownersCheck, ["--classify", "--", command], {
    stdio: "ignore",
    timeout: timeoutMs,
  });
  return run.status !== NOT_A_WRITE_STATUS;
}

/** True when COMMAND writes to GitHub: the pre-filter, then the classifier. */
export function isGithubWrite(command: string, ownersCheck: string = OWNERS_CHECK): boolean {
  return mightWriteGithub(command) && classifiesAsWrite(command, ownersCheck);
}
