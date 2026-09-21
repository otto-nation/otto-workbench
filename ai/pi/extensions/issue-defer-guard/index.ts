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
 * Both the predicate and the review probe live in ./detect.ts so they can be
 * tested without the SDK — see that file's header. What stays here is only the
 * wiring: this file is the part no test can reach.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { isIssueFiling, branchReviewHasOpenFindings } from "./detect.ts";

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
        "unrelated to the review, say so and file it. See general.md § Code Quality.",
    };
  });
}
