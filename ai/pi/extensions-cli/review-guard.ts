/**
 * Tool gating for the Pi review and fix agents.
 *
 * Blocks writes outside the review worktree, and logs every tool call as JSON
 * on stderr for cost attribution.
 *
 * Loaded per-invocation by ai/lib/agent/backend_pi.py:
 *
 *     pi --extension <repo>/ai/pi/extensions-cli/review-guard.ts
 *
 * Deliberately not in ai/pi/extensions/, which ai/pi/steps.sh installs into
 * ~/.pi/agent/extensions and Pi loads in every session. This one gates on
 * REVIEW_WORKTREE_DIR, which only the review pipeline sets, so installed
 * globally it would be inert in every interactive session while still writing a
 * log line for every tool call.
 *
 * With the variable unset it blocks nothing. That is the fail-open direction on
 * purpose: this is a guard rail for an agent already restricted by its prompt
 * and tool list, not the boundary the pipeline relies on. A typo in the
 * variable's name must not silently turn a review into an unrestricted session,
 * so the absence is reported once rather than passing quietly.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { isAbsolute, relative, resolve } from "node:path";

// Shell constructs that write. Matched against the whole command, so this is a
// coarse net: it catches the ordinary cases and accepts false positives on a
// command that merely mentions one. A review agent has no reason to run any of
// them, so refusing too much costs nothing here.
const WRITE_COMMAND_PATTERNS = [
  /\bcp\b/,
  /\bmv\b/,
  /\brm\b/,
  /\btee\b/,
  /\bdd\b/,
  /\btruncate\b/,
  /\bsed\s+-i/,
  /\bgit\s+(?:commit|push|checkout|reset|clean|stash|rebase|merge)\b/,
  />>?/,
];

/** True when `path` is inside `root` — the same directory, or below it. */
function within(root: string, path: string): boolean {
  const rel = relative(resolve(root), resolve(root, path));
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

export default function (pi: ExtensionAPI) {
  const worktreeDir = process.env.REVIEW_WORKTREE_DIR;
  let warned = false;

  pi.on("tool_call", async (event) => {
    if (!worktreeDir) {
      if (!warned) {
        warned = true;
        console.error(
          JSON.stringify({
            ts: new Date().toISOString(),
            guard: "inactive",
            reason: "REVIEW_WORKTREE_DIR is unset; writes are not gated",
          }),
        );
      }
      return;
    }

    let summary = "";
    let blocked: string | null = null;

    if (isToolCallEventType("write", event) || isToolCallEventType("edit", event)) {
      summary = event.input.path;
      if (!within(worktreeDir, event.input.path)) {
        blocked = `${event.input.path} is outside the review worktree ${worktreeDir}`;
      }
    } else if (isToolCallEventType("bash", event)) {
      summary = event.input.command.slice(0, 120);
      if (WRITE_COMMAND_PATTERNS.some((p) => p.test(event.input.command))) {
        blocked = `write-capable command in a review session: ${summary}`;
      }
    } else if (isToolCallEventType("read", event)) {
      summary = event.input.path;
    }

    console.error(
      JSON.stringify({
        ts: new Date().toISOString(),
        tool: event.toolName,
        summary,
        ...(blocked ? { blocked } : {}),
      }),
    );

    // terminate is left off: one refused write is a step the agent can route
    // around, not a reason to abandon a review that has already cost tokens.
    if (blocked) return { block: true, reason: blocked };
  });
}
