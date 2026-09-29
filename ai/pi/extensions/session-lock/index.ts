/**
 * Records this Pi session as editing its worktree.
 *
 * An unattended `pr` fix pass commits what it finds in a worktree. A person
 * editing in this session is a second writer the pass cannot see, and the
 * pass wins — a half-finished edit lands in a commit nobody reviewed. This
 * says the session is here; ai/lib/fix/engine.py is what reads it and
 * refuses, naming the holder rather than guessing from tree state.
 *
 * Its twin is ai/claude/bin/claude-session-lock. Both write through
 * `with-session-lock`, so one record answers for both harnesses — the same
 * arrangement tree-lock-guard has with claude-edit-guard, and for the same
 * reason: two harnesses inferring the fact separately is how they come to
 * disagree.
 *
 * The claim and its release live in ./detect.ts so they can be tested without
 * the SDK — see that file's header. What stays here is only the wiring.
 *
 * On /reload, /new and /fork Pi emits session_shutdown before the next
 * session_start, so the pair below is release-then-reclaim with the pid
 * unchanged. `acquire` replaces a same-pid record rather than appending, so
 * that round trip leaves one entry however many times it happens.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { acquireFor, releaseFor } from "./detect.ts";

export default function (pi: ExtensionAPI) {
  // The worktree this session claimed, or null. Held so shutdown releases the
  // directory that was actually recorded: a session whose cwd moved would
  // otherwise leave its first claim behind forever, and a stale claim refuses
  // every later pass on that tree.
  let held: string | null = null;

  pi.on("session_start", async (_event, ctx) => {
    if (held) return;
    const cwd = ctx.cwd;
    if (!cwd) return;
    // Through sessionManager, which is where the id lives — ExtensionContext
    // has no `sessionId` of its own, and reading one would quietly record an
    // empty string, costing Pi the session-id half of self-exemption.
    const sessionId = ctx.sessionManager?.getSessionId() ?? "";
    if (acquireFor(cwd, process.pid, sessionId)) {
      held = cwd;
    }
  });

  // Idempotent, as the extension docs require: a second shutdown with nothing
  // held must not release somebody else's claim on the same tree.
  pi.on("session_shutdown", async () => {
    if (!held) return;
    releaseFor(held, process.pid);
    held = null;
  });
}
