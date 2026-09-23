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
 * REVIEW_ALLOWED_DIRS carries the invocation's add_dirs, delimiter-separated.
 * The review document lives under ~/.local/state/workbench/reviews/, outside
 * the worktree by design — gating on the worktree alone would refuse the one
 * write every phase exists to make.
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
import { delimiter } from "node:path";
import { blockedWriteCommand, isScratchPath, within } from "./detect.ts";

// No `context` hook prunes the superpowers bootstrap here, though the shape of
// this extension invites one. The package injects it through its own `context`
// hook, but only when its skills were discovered: backend_pi's `--no-skills`
// already suppresses it, and a filter added here matched nothing on every
// probe. Measured preamble is identical with and without one, so what it would
// add is a hook that never fires and a marker string to keep in step with
// someone else's package.

// `canonical`, `within` and `isScratchPath` live in detect.ts, which imports no
// SDK and so loads under plain `node`: tests/pi_extensions.bats exercises the
// write gating directly rather than by grepping this file's source.

export default function (pi: ExtensionAPI) {
  const worktreeDir = process.env.REVIEW_WORKTREE_DIR;
  const allowedDirs = [
    ...(worktreeDir ? [worktreeDir] : []),
    ...(process.env.REVIEW_ALLOWED_DIRS ?? "").split(delimiter).filter(Boolean),
  ];
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
      const permitted =
        allowedDirs.some((dir) => within(dir, event.input.path)) ||
        isScratchPath(event.input.path);
      if (!permitted) {
        // Every allowed root, not just the worktree: a refusal that names one
        // of two permitted directories reads as a bug in the guard.
        blocked =
          `${event.input.path} is outside the review's writable directories: ` +
          `${allowedDirs.join(", ")}. A scratch file goes under /tmp, which is ` +
          `writable and is not in the commit scope.`;
      }
    } else if (isToolCallEventType("bash", event)) {
      summary = event.input.command.slice(0, 120);
      const offending = blockedWriteCommand(event.input.command);
      if (offending) {
        // The offending statement, not a slice of the whole command: a 120-char
        // summary truncated the trailing redirect that was the real match, so
        // the refusal looked like it had blocked the `cd` in front of it.
        // The scratch sentence names the route that actually exists. The
        // refusal used to prescribe only a redirect, which does not help an
        // agent trying to *delete* a file — and since `rm`, `mv` and `cp` are
        // all refused here, an agent that had written into the worktree had no
        // permitted way to clean up and left the file behind to be committed.
        blocked =
          `write-capable command in a review session — ${offending}. ` +
          `A review reads; it does not modify the tree. To run a suite, invoke it ` +
          `directly (\`pytest tests/foo.py\`) or redirect to /tmp ` +
          `(\`pytest tests/ > /tmp/out.txt 2>&1\`). A scratch file belongs under ` +
          `/tmp, where the write tool may create it and nothing needs deleting.`;
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
