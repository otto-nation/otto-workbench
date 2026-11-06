/**
 * Refuses edits to the installed copy of the workbench, and worktrees made from
 * it.
 *
 * `~/.local/share/otto-workbench` is what `otto-workbench sync` runs from. It
 * is a plain clone, so git lets a session that started there add worktrees to
 * it, and several branches' checkouts ended up beside the install instead of in
 * the container. A rule alone did not stop that; this does. Claude Code gets the
 * same rule from ai/claude/bin/claude-edit-guard and claude-bash-guard, against
 * the same checker (bin/local/installed-copy-check).
 *
 * The probe lives in ./detect.ts so it can be tested without the SDK.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { commandRefusal, editRefusal } from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    let reason: string | null = null;
    if (isToolCallEventType("edit", event) || isToolCallEventType("write", event)) {
      if (event.input.path) reason = editRefusal(event.input.path, ctx.cwd);
    } else if (isToolCallEventType("bash", event)) {
      reason = commandRefusal(event.input.command, ctx.cwd);
    }
    // terminate is left off deliberately, as in the other guards: the agent
    // should carry on and do the work in the container.
    if (reason) return { block: true, reason };
  });
}
