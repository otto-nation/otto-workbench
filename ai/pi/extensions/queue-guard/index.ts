/**
 * Refuses a heavy command run outside otto-queue in a repo listed in
 * `queue.heavy_repos`.
 *
 * Agents on one host start `golangci-lint` and `go test` at the same moment and
 * oversubscribe its cores; the repo they work in may not adopt otto-queue
 * itself, so the admission is asked of the agent's harness instead. The refusal
 * names the `otto-queue submit … -- …` then `otto-queue wait <id>` to re-issue.
 * Claude Code gets the same rule from ai/claude/bin/claude-bash-guard, against
 * the same checker (bin/local/queue-guard-check). With the list empty, which is
 * the default, this does nothing.
 *
 * The probe lives in ./detect.ts so it can be tested without the SDK.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { commandRefusal } from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (!isToolCallEventType("bash", event)) return;
    const reason = commandRefusal(event.input.command, ctx.cwd);
    // terminate is left off deliberately, as in the other guards: the agent
    // should re-issue the command through the queue and carry on.
    if (reason) return { block: true, reason };
  });
}
