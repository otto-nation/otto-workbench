/**
 * The piped-test-run predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file imports only ../_shared, which imports nothing, so
 * tests/pi_extensions.bats can still run it under plain `node`.
 *
 * What counts as a piped test run here is meant to match
 * ai/claude/bin/claude-bash-guard decision for decision: same runners, same
 * filters, same pipefail exemption. Two guards enforcing one rule that disagree
 * about a given command are worse than one guard, because which answer you get
 * depends on which harness you happen to be in.
 *
 * Splitting is the shared scan rather than a regex over the raw line, and that
 * is what closed the largest class of disagreement between the two harnesses:
 * a `|` inside quotes was read as a pipe, so `pytest tests/ | grep -q 'a|b'`
 * looked like a pipeline ending in `b'` instead of in `grep` and was allowed,
 * while Claude — which deletes quoted spans before splitting — refused it.
 */

import { splitStatements } from "../_shared/statements.ts";
import { tokenize } from "../_shared/tokenize.ts";

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
 *
 * Read from the scanned tokens rather than from the raw text, so a `pipefail`
 * that only ever appears inside quotes does not exempt anything:
 * `echo 'set -o pipefail'; pytest | tail` prints a string and sets nothing, and
 * a regex over the raw command exempted it here while Claude still refused it.
 * A statement's text carries its quotes, so matching that would repeat the bug
 * one level down.
 */
function setsPipefail(statement: string): boolean {
  const words = tokenize(statement).filter((t) => !t.operator && !t.quoted);
  for (let i = 0; i + 2 < words.length; i++) {
    if (
      words[i].value === "set" &&
      /^-[a-zA-Z]*o$/.test(words[i + 1].value) &&
      words[i + 2].value === "pipefail"
    ) {
      return true;
    }
  }
  return false;
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
  const split = splitStatements(command);
  if (split.some((s) => setsPipefail(s.text))) return null;

  // A run of statements joined by `|` is one pipeline. Regrouped from the
  // shared scan's flag rather than by re-splitting on `|`, which would cut a
  // quoted one and read the wrong stage as last.
  let stages: string[] = [];
  for (const statement of split) {
    stages.push(statement.text);
    if (statement.pipedIntoNext) continue;

    if (stages.length >= 2 && isFilter(stages[stages.length - 1])) {
      // The last stage decides: an intermediate filter still leaves the final
      // status to whatever ends the pipeline, and it is the end that `$?` reads.
      const runner = stages.slice(0, -1).find(invokesRunner);
      if (runner !== undefined) {
        const tokens = runner.trim().split(/\s+/).filter(Boolean);
        const named = tokens.find((t) => !t.includes("="));
        return named === undefined ? null : basename(named);
      }
    }
    stages = [];
  }
  return null;
}

/** Whether `command` pipes a test runner's status into a filter that discards it. */
export function isPipedTestRun(command: string): boolean {
  return pipedRunner(command) !== null;
}
