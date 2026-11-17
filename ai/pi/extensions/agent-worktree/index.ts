/**
 * Publishes the worktree an interactive Pi session works in on its herdr
 * terminal (`hc label <terminal> otto.worktree=<path>`), and claims the reached
 * worktree through the same hook, in every interactive session.
 *
 * Claude Code gets the same from ai/claude/settings.json's PostToolUse and
 * SessionStart entries; those hooks do not run under Pi. Both harnesses run
 * the one hook, ai/bin/report-agent-worktree, with a Claude-shaped payload —
 * which worktree a call targeted is ai/lib/pr/agent_worktree.py's answer, and
 * it needs git resolution two copies would get wrong differently.
 *
 * `tool_result` rather than `tool_call`: a `wt switch -c` worktree does not
 * exist until the command has run. Fails open and blocks nothing: the tool
 * call already succeeded, and a stale label is harmless.
 */

import { type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFile } from "node:child_process";
import {
  HOOK_PATH, hookEnv, hookSession, payloadFor, shouldReport, type HookSession,
} from "./detect.ts";

/**
 * Run the hook with ARGS, writing STDIN to it. Unawaited: the turn must not
 * wait on a label, and a failure here is not the agent's problem.
 *
 * The payload goes through `child.stdin` — `execFile` has no `input` option,
 * and passing one leaves the hook blocked on a stdin that never closes. The
 * `error` handler swallows the EPIPE of a hook that exited before reading (it
 * does, outside a herdr terminal), which would otherwise be an unhandled
 * error event. `timeout` is the backstop for a hook that blocks.
 */
function runHook(
  args: string[], stdin: string, cwd: string, session: HookSession | null,
): void {
  const env = hookEnv(process.env, cwd, session);
  const child = execFile("bash", [HOOK_PATH, ...args], { timeout: 10_000, env }, () => {});
  child.stdin?.on("error", () => {});
  child.stdin?.end(stdin);
}

export default function (pi: ExtensionAPI) {
  pi.on("session_start", async (_event, ctx) => {
    // A new session in a reused terminal must not inherit the last one's claim.
    if (!shouldReport(ctx.mode)) return;
    const sessionId = ctx.sessionManager?.getSessionId() ?? "";
    const session = hookSession(process.env, process.pid, sessionId);
    runHook(["--clear"], "", ctx.cwd, session);
  });

  pi.on("tool_result", async (event, ctx) => {
    if (event.isError || !shouldReport(ctx.mode)) return;
    const payload = payloadFor(event, ctx.cwd);
    if (payload === null) return;
    const sessionId = ctx.sessionManager?.getSessionId() ?? "";
    const session = hookSession(process.env, process.pid, sessionId);
    runHook([], JSON.stringify(payload), ctx.cwd, session);
  });
}
