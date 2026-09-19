/**
 * The piped-test-run predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file imports nothing, which is what lets tests/pi_extensions.bats run it under
 * plain `node` and assert the shapes it does and does not match.
 *
 * What counts as a piped test run here is meant to match
 * ai/claude/bin/claude-bash-guard decision for decision: same runners, same
 * filters, same pipefail exemption. Two guards enforcing one rule that disagree
 * about a given command are worse than one guard, because which answer you get
 * depends on which harness you happen to be in.
 */

/**
 * The commands whose exit status is the thing a caller is reading.
 *
 * Test runners and validators — the ones where "did it pass" is the whole
 * question and the output is secondary. A build or a formatter is not here: its
 * output is often the point, and piping one is ordinary.
 *
 * Matched on the last path segment, so `bin/local/run-tests` and a bare
 * `run-tests` are the same command. Kept in step with TEST_RUNNERS in
 * ai/claude/bin/claude-bash-guard — tests/pi_extensions.bats holds the two lists
 * to one set rather than letting the harnesses drift.
 */
export const TEST_RUNNERS = [
  "pytest",
  "bats",
  "run-tests",
  "validate-all",
  "go",
  "cargo",
  "npm",
  "pnpm",
  "yarn",
  "jest",
  "vitest",
];

/**
 * The runners above that are general-purpose build tools too — `go build`,
 * `npm run dev` — where the output is often the point and piping one is
 * ordinary. For these, bare invocation isn't enough: the test subcommand
 * itself has to be present. Kept in step with BUILD_STYLE_RUNNERS in
 * ai/claude/bin/claude-bash-guard.
 */
const SUBCOMMAND_REQUIRED = ["go", "cargo", "npm", "pnpm", "yarn"];

/**
 * The filters that discard the status they were handed.
 *
 * Every one of these exits on its own terms — `tail` succeeds on empty input —
 * so the pipeline reports whether the *filter* ran, not whether the suite
 * passed. A pipe into a pager or a file-writer is not this: `tee` keeps the
 * whole run and is a reasonable thing to do, though the status is still the
 * filter's, so it is listed too.
 */
const FILTERS = [
  "tail",
  "head",
  "grep",
  "egrep",
  "rg",
  "sed",
  "awk",
  "wc",
  "tee",
  "cut",
  "sort",
  "uniq",
];

/** Opens a heredoc, capturing the `-` that allows an indented terminator and the marker. */
const HEREDOC_OPEN = /<<(-?)\s*['"]?([A-Za-z_][A-Za-z0-9_]*)/;

/**
 * Whether the command turns on `pipefail` before the pipe runs.
 *
 * The one narrow way a piped suite still reports its own failure: with it set,
 * the pipeline takes the first failing stage's status. Accepted anywhere in the
 * command rather than only at the head, because `set -o pipefail` on its own
 * line above the run is the ordinary spelling.
 *
 * `set -[a-zA-Z]*o pipefail` covers both the long form and a bundled short
 * flag (`set -eo pipefail`), since the letters before the required `o` are
 * unconstrained and `-o` alone is the zero-letter case of the same pattern.
 */
function hasPipefail(command: string): boolean {
  return /\bset\s+-[a-zA-Z]*o\s+pipefail\b/.test(command);
}

/**
 * Every line of `command`, with heredoc bodies dropped.
 *
 * A heredoc body is content being written to a file, not commands, so a piped
 * `pytest` inside one is not an invocation — writing a script for someone else
 * to run is not the agent piping a suite. Claude's guard skips those lines too,
 * and this is the same rule.
 *
 * Only `<<-` lets the terminator be indented. Accepting indentation for a plain
 * `<<` would end the body early on a body line that happens to be the marker
 * word, and scan the rest of it as commands.
 */
function lines(command: string): string[] {
  const found: string[] = [];
  let terminator: RegExp | null = null;

  for (const line of command.split("\n")) {
    if (terminator) {
      if (terminator.test(line)) terminator = null;
      continue;
    }
    found.push(line);

    const open = HEREDOC_OPEN.exec(line);
    if (open) {
      const indentable = open[1] ? "\\s*" : "";
      terminator = new RegExp(`^${indentable}${open[2]}\\s*$`);
    }
  }
  return found;
}

/** The last path segment of a token, so `bin/local/run-tests` reads as `run-tests`. */
function basename(token: string): string {
  const cleaned = token.replace(/^['"]|['"]$/g, "");
  return cleaned.slice(cleaned.lastIndexOf("/") + 1);
}

/**
 * Whether a pipeline stage invokes a test runner.
 *
 * The runner has to be the command being run, not an argument to something
 * else: `grep pytest notes.md` names one and invokes nothing. Leading
 * environment assignments are skipped so `WORKBENCH_X=1 pytest` still
 * matches — a `sudo`-style prefix is not, so `sudo pytest` does not.
 *
 * A SUBCOMMAND_REQUIRED runner is also a general-purpose build tool, so the
 * bare name isn't enough — `go test`/`cargo test` directly, or `npm`/`pnpm`/
 * `yarn` via `test`/`run test`/`run-script test`. Anything else (`go build`,
 * `npm run dev`) is left alone: the output is often the point, and piping one
 * is ordinary.
 */
function invokesRunner(stage: string): boolean {
  const tokens = stage.trim().split(/\s+/).filter(Boolean);
  let name: string | undefined;
  let rest: string[] = [];
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i].includes("=")) continue;
    name = basename(tokens[i]);
    rest = tokens.slice(i + 1);
    break;
  }
  if (name === undefined || !TEST_RUNNERS.includes(name)) return false;
  if (!SUBCOMMAND_REQUIRED.includes(name)) return true;

  const nonflag = rest.filter((t) => !t.startsWith("-"));
  if (nonflag[0] === "test") return true;
  if (
    (name === "npm" || name === "pnpm" || name === "yarn") &&
    (nonflag[0] === "run" || nonflag[0] === "run-script") &&
    nonflag[1] === "test"
  ) {
    return true;
  }
  return false;
}

/** Whether a pipeline stage is one of the status-discarding filters. */
function isFilter(stage: string): boolean {
  const tokens = stage.trim().split(/\s+/).filter(Boolean);
  const first = tokens[0];
  return first !== undefined && FILTERS.includes(basename(first));
}

/**
 * The test runner whose status a filter is about to discard, or null.
 *
 * Split on a single `|` only: `||` is a control operator, not a pipe, so
 * `pytest tests/ || echo failed` is a fallback and reports the suite's own
 * status. Splitting naively on `|` would read it as a pipe into `| echo`.
 */
export function pipedRunner(command: string): string | null {
  if (hasPipefail(command)) return null;

  for (const line of lines(command)) {
    // Statement separators first: each is its own pipeline.
    for (const statement of line.split(/;|&&|\|\|/)) {
      const stages = statement.split("|");
      if (stages.length < 2) continue;
      // The last stage decides: an intermediate filter still leaves the final
      // status to whatever ends the pipeline, and it is the end that `$?` reads.
      if (!isFilter(stages[stages.length - 1])) continue;
      const runner = stages.find(invokesRunner);
      if (runner === undefined) continue;
      const tokens = runner.trim().split(/\s+/).filter(Boolean);
      const named = tokens.find((t) => !t.includes("="));
      return named === undefined ? null : basename(named);
    }
  }
  return null;
}

/** Whether `command` pipes a test runner's status into a filter that discards it. */
export function isPipedTestRun(command: string): boolean {
  return pipedRunner(command) !== null;
}
