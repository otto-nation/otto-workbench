/**
 * Refuses a bash command that detaches a shell with `&` or `nohup`.
 *
 * Pi has `job_start`, which runs the work, bounds it with `timeout_minutes`, and
 * messages the agent when it exits. A shell detached with `&` does none of that:
 * nothing reports its completion, so the agent falls to polling it, and nothing
 * owns it, so stopping it means hunting a pid. The observed failure is a rebase
 * launched with `nohup ... > ignore/rebase.json &`, followed by a sleep, a poll,
 * and a turn ending in "ping me and I'll check" — for a job that had already
 * finished.
 *
 * Claude Code gets the same rule from ai/claude/bin/claude-bash-guard, where it
 * also prevents a permission prompt; that hook does not run under Pi. The two
 * enforce one rule written down in general.md § Waiting on Background Work.
 *
 * Installed globally by ai/pi/steps.sh, which is correct for this one: it needs
 * no environment set up, has nothing to say about the repository it is in, and
 * the behaviour it prevents is not specific to any session.
 *
 * The predicate lives in ./detect.ts so it can be tested without the SDK — see
 * that file's header.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { isDetachedBackground } from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    if (!isToolCallEventType("bash", event)) return;
    if (!isDetachedBackground(event.input.command)) return;

    // terminate is left off deliberately, as in sleep-guard: the agent should
    // carry on with the turn having started the job properly instead.
    return {
      block: true,
      reason:
        "Backgrounding with & or nohup detaches a shell nothing reports on, so the only " +
        "way left to learn it finished is to poll it. Use job_start instead — it messages " +
        "you on completion and takes timeout_minutes to bound the run. Redirecting the " +
        "output to a file in the repo is part of the same pattern: job_start returns it. " +
        "See general.md § Waiting on Background Work.",
    };
  });
}
