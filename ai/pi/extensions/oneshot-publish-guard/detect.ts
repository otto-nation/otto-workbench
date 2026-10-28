import { isGithubWrite, OWNERS_CHECK } from "../_shared/github-writes.ts";
import { statements } from "../_shared/statements.ts";
import { tokenize } from "../_shared/tokenize.ts";

/**
 * The publishing predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file pulls in nothing but ../_shared, which imports only Node built-ins, so
 * tests/pi_extensions_oneshot_publish.bats can run it under plain `node`.
 *
 * What counts as publishing is `linear issue create` plus every GitHub write
 * ../_shared/github-writes.ts recognises — one write classifier, so this rule
 * and the owner boundary cannot disagree about what a write is. It matches
 * `_command_publishes` in ai/claude/bin/claude-bash-guard decision for
 * decision. Two guards enforcing one rule that disagree about a given command
 * are worse than one guard, because which answer you get depends on which
 * harness you happen to be in. The *when* differs: this side keys on
 * json/print mode (a one-shot session); Claude keys on a non-empty `agent_id`.
 */

/**
 * True when STATEMENT files a Linear issue.
 *
 * Its own case because the GitHub classifier does not know Linear. Anchored
 * to the command being run, so `echo 'linear issue create'` is not matched.
 *
 * ceiling: `linear` reached through a wrapper (`env linear …`) or an alias is
 * not matched. Upgrade if one shows up in an agent transcript; the Claude
 * guard reads it the same way.
 */
function filesLinearIssue(statement: string): boolean {
  const words = tokenize(statement)
    .filter((token) => !token.operator)
    .map((token) => token.value);
  return words[0] === "linear" && words[1] === "issue" && words[2] === "create";
}

/**
 * True when COMMAND files an issue or writes to GitHub.
 *
 * OWNERS_CHECK is the classifier the GitHub half asks; a test passes another
 * to exercise the fail-closed path.
 */
export function isPublishing(command: string, ownersCheck: string = OWNERS_CHECK): boolean {
  if (statements(command).some(filesLinearIssue)) return true;
  return isGithubWrite(command, ownersCheck);
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
