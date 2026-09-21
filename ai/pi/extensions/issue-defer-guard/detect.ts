import { statements } from "../_shared/statements.ts";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

/**
 * Both halves of the guard's decision, kept apart from the extension using them.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file pulls in ../_shared and two node builtins, all of which resolve without
 * the SDK, so tests/pi_extensions.bats can run it under plain `node`.
 *
 * The filesystem probe lives here rather than in index.ts for exactly that
 * reason: it is the half most likely to break silently — a renamed review
 * directory or a moved state root makes it answer false forever, which is a
 * guard that has stopped working without failing. Left in index.ts it would be
 * unreachable by any test.
 *
 * What counts as filing here is meant to match the `gh issue create` rule in
 * ai/claude/bin/claude-bash-guard decision for decision, and the probe mirrors
 * that guard's `_branch_review_has_open_findings`. Two guards enforcing one
 * rule that disagree about a given command are worse than one guard, because
 * which answer you get depends on which harness you happen to be in.
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

/** An unticked checkbox, the shape `pr review` writes an unresolved finding in. */
const OPEN_FINDING = /^- \[ \]/m;

/** A git command's stdout, or "" when git cannot answer. */
export function git(args: string[]): string {
  try {
    return execFileSync("git", args, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "";
  }
}

/**
 * True when a self-review for the current branch is sitting on open findings.
 *
 * The directory name is the one ai/lib/cli/claude_review.py builds:
 * <repo>-self-<branch with / replaced by ->. The repo half comes from the
 * remote rather than the directory, because every worktree of one repo has its
 * own basename and they all review into the same name.
 *
 * The state root default matches WORKBENCH_STATE_DIR in lib/roots.sh.
 */
export function branchReviewHasOpenFindings(): boolean {
  const origin = git(["remote", "get-url", "origin"]);
  if (!origin) return false;
  const repo = origin.replace(/\.git$/, "").split("/").pop();
  if (!repo) return false;

  const branch = git(["rev-parse", "--abbrev-ref", "HEAD"]);
  if (!branch || branch === "HEAD") return false;

  const stateDir =
    process.env.WORKBENCH_STATE_DIR ??
    `${process.env.HOME}/.local/state/workbench`;
  const reviewFile =
    `${stateDir}/reviews/${repo}-self-${branch.replaceAll("/", "-")}/review.md`;

  try {
    return OPEN_FINDING.test(readFileSync(reviewFile, "utf8"));
  } catch {
    return false;
  }
}
