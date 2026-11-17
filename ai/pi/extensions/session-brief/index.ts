/**
 * session-brief: keeps a title and a short TL;DR on every interactive pi session,
 * with the worktrees and PRs it touched, so a session can be closed and found
 * again (the Sessions herdr panel reads them through `pi-sessions list`). When
 * and how: wire.ts and the `session-brief` section of ../README.md.
 */

// ceiling: pi only — Claude Code sessions get no brief; add the Claude Code twin
// (a hook writer plus a ~/.claude/projects/ reader in pi-sessions) once Claude
// Code sessions run on the agent hosts.

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readTiersText } from "./detect.ts";
import { wire } from "./wire.ts";

export default function (pi: ExtensionAPI) {
  wire(pi, {
    env: process.env,
    pid: process.pid,
    now: () => new Date(),
    // unref: an armed idle timer must not keep a quitting pi alive.
    setTimer: (fn, ms) => setTimeout(fn, ms).unref(),
    clearTimer: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    tiersText: readTiersText,
  });
}
