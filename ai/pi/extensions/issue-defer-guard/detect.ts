import { statements } from "../_shared/statements.ts";

/**
 * The issue-filing predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file pulls in nothing but ../_shared, which imports nothing itself, so
 * tests/pi_extensions.bats can run it under plain `node` and assert the shapes
 * it does and does not match.
 *
 * What counts as filing here is meant to match the `gh issue create` rule in
 * ai/claude/bin/claude-bash-guard decision for decision. Two guards enforcing
 * one rule that disagree about a given command are worse than one guard,
 * because which answer you get depends on which harness you happen to be in.
 *
 * Only the command half lives here. Whether the branch is under a review with
 * open findings is a filesystem question, so index.ts asks it — the same split
 * the Claude guard makes between its pattern and `_branch_review_has_open_findings`.
 */

/**
 * `gh issue create` as the command being run.
 *
 * Anchored to a statement head so `gh issue view`, `gh issue list`, and a grep
 * over a script mentioning the phrase are not matched. The subcommand words may
 * be separated by any run of whitespace, which is the only variation the form
 * takes in practice.
 *
 * Tested against one statement at a time (see ../_shared/statements.ts), so the
 * head is a plain `^\s*` — the leading whitespace a separator like `&& ` or
 * `; ` leaves behind on the fragment after it, rather than the separator
 * itself. That is also what catches a filing on the second line of a command
 * whose first line is a bare `cd`, the sanctioned form for a compound cd.
 *
 * ceiling: `gh` reached through an alias or a wrapper script is not matched.
 * Upgrade if one shows up; `gh issue create` is the form an agent writes, and
 * the Claude guard reads it the same way.
 */
const ISSUE_CREATE = /^\s*gh\s+issue\s+create(\s|$)/;

/** True when COMMAND files a new issue. */
export function isIssueFiling(command: string): boolean {
  return statements(command).some((statement) => ISSUE_CREATE.test(statement));
}
