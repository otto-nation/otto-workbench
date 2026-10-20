/**
 * Refuses a bash `sleep` used to wait for work that reports its own completion.
 *
 * Pi's `job_start` already tells the agent it will be messaged when the job
 * finishes and not to poll for it, and the agent runs `sleep 295` anyway. Prose
 * in the context file did not stop it; this does. The reason names `job_start`
 * only when `shouldAdviseJobStart` says so — the tool is listed and the session
 * is not json/print. Otherwise the alternative is a foreground bash call. See
 * ../_shared/wait-guidance.ts. Claude Code gets the
 * same rule from ai/claude/bin/claude-bash-guard, which does not run under Pi —
 * the two enforce one rule written down in general.md § Waiting on Background Work.
 *
 * Installed globally by ai/pi/steps.sh, which is correct for this one: it needs
 * no environment set up, has nothing to say about the repository it is in, and
 * the behaviour it prevents is not specific to any session. That is what
 * separates it from ../../extensions-cli/review-guard.ts, which gates on a
 * variable only the review pipeline sets.
 *
 * The predicate lives in ./detect.ts so it can be tested without the SDK — see
 * that file's header.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import {
  shouldAdviseJobStart,
  sleepSeconds,
  THRESHOLD_SECONDS,
  waitBlockReason,
} from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (!isToolCallEventType("bash", event)) return;

    const seconds = sleepSeconds(event.input.command);
    if (seconds === null || seconds < THRESHOLD_SECONDS) return;

    // terminate is left off deliberately: the agent should carry on with the
    // turn having skipped the wait, which is the whole point. Stopping it here
    // would make the guard more disruptive than the sleep it refuses.
    return {
      block: true,
      reason: waitBlockReason(
        "sleep",
        shouldAdviseJobStart(pi.getActiveTools(), ctx.mode),
        seconds,
      ),
    };
  });
}
