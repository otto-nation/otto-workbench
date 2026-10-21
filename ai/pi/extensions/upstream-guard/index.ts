/**
 * Refuses a bash command that writes to GitHub outside the owners in
 * github.owners — in every session mode, unlike oneshot-publish-guard.
 *
 * general.md § Upstream Repositories says the user posts upstream, never the
 * agent; prose did not hold. Claude Code gets the same rule from
 * ai/claude/bin/claude-bash-guard, and the workbench's own publishing is held
 * to it by gh.client. One policy behind all three: ai/lib/gh/owners.py.
 *
 * Installed globally by ai/pi/steps.sh: the behaviour it prevents is not
 * specific to any checkout, and a session with no repository is exactly where
 * a write's target cannot be resolved — which refuses.
 *
 * The refusal lives in ./detect.ts so it can be tested without the SDK. What
 * stays here is only the wiring.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { upstreamRefusal } from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (!isToolCallEventType("bash", event)) return;
    const reason = upstreamRefusal(event.input.command, ctx.cwd ?? process.cwd());
    if (!reason) return;
    // terminate is left off, as in the other guards: the agent carries on,
    // drafting the command for the user instead.
    return { block: true, reason };
  });
}
