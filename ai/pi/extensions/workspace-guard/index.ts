/**
 * Refuses a plan or spec written inside a checkout instead of the repository's
 * workspace.
 *
 * A plan is about the repository, not about one branch of it. Written in a
 * worktree it is duplicated across every sibling checkout, invisible from the
 * others, and destroyed by the `wt remove` that retires the branch — taking the
 * reasoning with it while the work it explains lives on in main. The workspace
 * sits at the container, above every checkout, and `bin/resolve-workspace` is
 * the one thing that names it.
 *
 * A rule alone cannot hold this. The vendored brainstorming skill states
 * `docs/superpowers/specs/` in its own prose and a rule can only ask it to
 * prefer another path — so the guard is the half that makes the answer
 * binding. Claude Code applies the same rule from ai/claude/bin/claude-edit-guard,
 * against the same resolver, so neither harness can name a different location.
 *
 * Pi's edit and write tools name their target `path`; Claude's use `file_path`.
 * That difference is why this is a separate reader rather than a shared binary,
 * along with `pi.exec()` taking no stdin where Claude hooks are stdin-JSON.
 *
 * The probe and the refusal live in ./detect.ts so they can be tested without
 * the SDK — see that file's header. What stays here is only the wiring.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { workspaceRefusal } from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    const path =
      isToolCallEventType("edit", event) || isToolCallEventType("write", event)
        ? event.input.path
        : undefined;
    if (!path) return;

    const reason = workspaceRefusal(path);
    if (!reason) return;

    // terminate is left off deliberately, as in the other guards: the agent
    // should carry on with the turn and write the file where it belongs,
    // rather than having the run ended under it.
    return { block: true, reason };
  });
}
