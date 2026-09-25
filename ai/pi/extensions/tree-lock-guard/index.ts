/**
 * Refuses an edit to a tree a validator is currently reading.
 *
 * Editing the working tree while a gate job validates it silently invalidates
 * the run. The job reports against a tree that no longer exists, and nothing in
 * its output says so — a green gate is exactly as green when its inputs changed
 * underneath it, so neither the operator nor the agent that caused it can tell
 * a real pass from a stale one.
 *
 * The validator declares itself by holding a lock (ai/lib/core/tree_lock.py);
 * this reads it. Claude Code reads the same lock from
 * ai/claude/bin/claude-edit-guard. The fact is one file, so the two harnesses
 * cannot answer differently — which is the whole reason the lock exists rather
 * than each guard inferring a validating job from running process names.
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
import { lockRefusal } from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    const path =
      isToolCallEventType("edit", event) || isToolCallEventType("write", event)
        ? event.input.path
        : undefined;
    if (!path) return;

    const reason = lockRefusal(path);
    if (!reason) return;

    // terminate is left off deliberately, as in the other guards: the agent
    // should carry on with the turn, waiting or moving to another worktree,
    // rather than having the run ended under it.
    return { block: true, reason };
  });
}
