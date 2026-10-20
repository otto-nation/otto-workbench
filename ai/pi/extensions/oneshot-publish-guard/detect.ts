import { statements } from "../_shared/statements.ts";
import { tokenize } from "../_shared/tokenize.ts";

/**
 * The publishing predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file pulls in nothing but ../_shared, which imports nothing itself, so
 * tests/pi_extensions_oneshot_publish.bats can run it under plain `node`.
 *
 * What counts as publishing is meant to match `_statement_publishes` in
 * ai/claude/bin/claude-bash-guard decision for decision. Two guards enforcing
 * one rule that disagree about a given command are worse than one guard,
 * because which answer you get depends on which harness you happen to be in.
 * The *when* differs: this side keys on json/print mode (a one-shot session);
 * Claude keys on a non-empty `agent_id`.
 */

const MUTATING_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);
const GH_PR_WRITES = new Set(["edit", "comment", "review", "create"]);
const ISSUE_OR_PULL = /(?:^|\/)(?:issues|pulls)(?:\/|$)/;

/** Words a statement will pass along, operators dropped. */
function wordsOf(statement: string): string[] {
  return tokenize(statement)
    .filter((token) => !token.operator)
    .map((token) => token.value);
}

/** True when WORDS name a mutating HTTP method via `-X` / `--method`. */
function mutatingMethod(words: string[]): boolean {
  for (let i = 0; i < words.length; i++) {
    const word = words[i];
    if (word === "-X" || word === "--method") {
      return MUTATING_METHODS.has((words[i + 1] ?? "").toUpperCase());
    }
    if (word.startsWith("--method=")) {
      return MUTATING_METHODS.has(word.slice("--method=".length).toUpperCase());
    }
    if (word.startsWith("-X") && word.length > 2) {
      return MUTATING_METHODS.has(word.slice(2).toUpperCase());
    }
  }
  return false;
}

/** True when a word is a GitHub path that names issues or pulls. */
function pathTouchesIssuesOrPulls(words: string[]): boolean {
  return words.some((word) => ISSUE_OR_PULL.test(word));
}

/**
 * True when STATEMENT files an issue or writes to a PR.
 *
 * Anchored to the command being run so a grep over a script, or
 * `echo 'gh issue create'`, is not matched. `pr comments --triage` and a GET
 * `gh api …/issues` are reads and are not matched.
 *
 * ceiling: `gh`/`pr`/`linear` reached through an alias or a wrapper script is
 * not matched. Upgrade if one shows up; the forms an agent writes are the
 * ones listed, and the Claude guard reads them the same way.
 */
function statementPublishes(statement: string): boolean {
  const words = wordsOf(statement);
  if (words[0] === "gh" && words[1] === "issue" && words[2] === "create") {
    return true;
  }
  if (words[0] === "linear" && words[1] === "issue" && words[2] === "create") {
    return true;
  }
  if (words[0] === "gh" && words[1] === "pr" && GH_PR_WRITES.has(words[2] ?? "")) {
    return true;
  }
  if (words[0] === "pr" && words[1] === "create") return true;
  if (words[0] === "pr" && words[1] === "comments" && words.includes("--post")) {
    return true;
  }
  if (words[0] === "pr" && words[1] === "describe" && words.includes("--post")) {
    return true;
  }
  if (
    words[0] === "gh" &&
    words[1] === "api" &&
    mutatingMethod(words.slice(2)) &&
    pathTouchesIssuesOrPulls(words.slice(2))
  ) {
    return true;
  }
  return false;
}

/** True when COMMAND files an issue or writes to a PR. */
export function isPublishing(command: string): boolean {
  return statements(command).some((statement) => statementPublishes(statement));
}

/**
 * True when MODE is a one-shot session.
 *
 * Subagents run as `pi --mode json -p`. Print mode is the same shape without
 * the JSON wrapper. Interactive (`tui`) and workbench automation (`rpc`) are
 * not this: see ai/lib/agent/backend_pi.py `_build_agent_cmd` / `_build_fix_cmd`
 * (`--mode rpc`) and `_build_prompt_cmd` (`--mode json` with `--no-tools`).
 */
export function isOneshotSession(mode: string): boolean {
  return mode === "json" || mode === "print";
}
