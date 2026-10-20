/**
 * Refuses bash commands that file an issue or write to a PR in a one-shot
 * session (`ctx.mode` is `json` or `print`).
 *
 * A dispatched subagent runs as `pi --mode json -p`. Its work is reviewed by
 * its parent; it should report deferrals and proposed PR text, not file or
 * edit. Prose in the prompt ("never post to GitHub") did not hold: a subagent
 * followed issue-tracker.md's "Nothing is truly out of scope" and filed
 * anyway. This is the mechanical half.
 *
 * Silent in every other mode. Workbench automation is unaffected because its
 * agents run Pi in rpc mode — `_build_agent_cmd` and `_build_fix_cmd` in
 * ai/lib/agent/backend_pi.py (the argv starts `*pi, "--mode", "rpc", …`). The
 * only json-mode call there is `_build_prompt_cmd`, which passes `--no-tools`
 * (`*pi, "-p", "--mode", "json", "--no-session", "--approve", "--no-tools"`),
 * so this handler never sees a bash call from it.
 *
 * Claude Code gets the same command set from ai/claude/bin/claude-bash-guard
 * when `agent_id` is non-empty; that hook does not run under Pi. The two
 * enforce one rule written down in git-operations.md § Subagent Publishing.
 *
 * Installed globally by ai/pi/steps.sh. A one-shot session has no repository
 * environment of its own, and the behaviour this prevents is not specific to
 * any checkout.
 *
 * The predicate lives in ./detect.ts so it can be tested without the SDK — see
 * that file's header. What stays here is only the wiring.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { isOneshotSession, isPublishing } from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (!isOneshotSession(ctx.mode)) return;
    if (!isToolCallEventType("bash", event)) return;
    if (!isPublishing(event.input.command)) return;

    // terminate is left off deliberately, as in the other guards: the agent
    // should carry on with the turn having reported the deferral instead.
    return {
      block: true,
      reason:
        "A dispatched subagent does not file issues or post to a PR — report " +
        "the deferral (what, why, suggested title) and any proposed PR text to " +
        "the parent, which files or edits. See git-operations.md § Subagent Publishing.",
    };
  });
}
