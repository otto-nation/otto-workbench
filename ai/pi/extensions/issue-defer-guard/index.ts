/**
 * Refuses `gh issue create` while the current branch's self-review still has
 * open findings.
 *
 * Filing an issue for a finding you could fix now is a deferral, and deferring
 * needs the operator's agreement. It does not read like one from the inside:
 * filing feels like diligence, the issue is a real artifact, and "tracked
 * rather than dropped" sounds like the responsible answer. The observed failure
 * is an issue filed for a review finding while the review's own fix pass was
 * concurrently implementing it — two actors on one findings list, and a
 * backlog entry created for work that was already being done.
 *
 * Claude Code gets the same rule from ai/claude/bin/claude-bash-guard; that
 * hook does not run under Pi. The two enforce one rule written down in
 * general.md § Code Quality and self-review.md § Before PR Creation.
 *
 * Narrow on purpose, and fails open at every step. No git, no remote, no
 * review, or a review whose findings are all ticked all mean silence — a guard
 * that blocked because it could not answer would be worse than the mistake it
 * prevents. Unrelated filing during a review costs one explanation, which is
 * the trade for catching the case the guard exists for.
 *
 * The predicate lives in ./detect.ts so it can be tested without the SDK — see
 * that file's header.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { isIssueFiling } from "./detect.ts";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

/** An unticked checkbox, the shape `pr review` writes an unresolved finding in. */
const OPEN_FINDING = /^- \[ \]/m;

/** A git command's stdout, or "" when git cannot answer. */
function git(args: string[]): string {
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
function branchReviewHasOpenFindings(): boolean {
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

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    if (!isToolCallEventType("bash", event)) return;
    if (!isIssueFiling(event.input.command)) return;
    if (!branchReviewHasOpenFindings()) return;

    // terminate is left off deliberately, as in the other guards: the agent
    // should carry on with the turn having addressed the finding instead.
    return {
      block: true,
      reason:
        "This branch's self-review has open findings, and filing an issue for one is a " +
        "deferral that needs the operator's agreement first. Read what the fix pass did " +
        "before acting on any finding yourself — it works the same list you do, and may " +
        "be committing this one as you write the issue. Having measured the cost and " +
        "found it small argues for doing the work, not for filing it. If this issue is " +
        "unrelated to the review, say so and file it. See general.md § Code Quality and " +
        "self-review.md § Before PR Creation.",
    };
  });
}
