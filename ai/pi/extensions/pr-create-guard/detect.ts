/**
 * The `gh pr create` predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file imports only ../_shared, which imports nothing, so
 * tests/pi_extensions.bats can run it under plain `node`.
 *
 * Why this rule is shared where twelve of Claude's are not: the Claude-only
 * ones exist to dodge that harness's permission-prompt engine, and each of
 * their block messages names a prompt or a static-analysis failure. Pi has no
 * allow-list to key on, so porting them would add noise that blocks nothing.
 * This one names neither. It says `task pr:create` loads the repo's PR
 * template, applies the template check, appends the closing refs and assigns
 * the PR — none of which `gh pr create` does, under any harness. That is
 * coding policy, written down in git-operations.md § PR Creation, and it
 * applies here verbatim.
 */

import { statements } from "../_shared/statements.ts";

/**
 * `gh pr create` as the command being run.
 *
 * Anchored to a statement head so `gh pr view`, `gh pr list`, and a grep over a
 * script mentioning the phrase are not matched. The Claude guard tested the raw
 * command for the substring, which refused `echo gh pr create` and a grep whose
 * pattern happened to contain it; both harnesses now read the statement.
 *
 * Tested against one statement at a time (see ../_shared/statements.ts), so the
 * head is a plain `^\s*` — the leading whitespace a separator like `&& ` leaves
 * on the fragment after it, rather than the separator itself.
 *
 * ceiling: `gh` reached through an alias or a wrapper script is not matched.
 * Upgrade if one shows up; `gh pr create` is the form an agent writes, and the
 * Claude guard reads it the same way.
 */
const PR_CREATE = /^\s*gh\s+pr\s+create(\s|$)/;

/** True when COMMAND opens a pull request directly. */
export function isPrCreate(command: string): boolean {
  return statements(command).some((statement) => PR_CREATE.test(statement));
}
