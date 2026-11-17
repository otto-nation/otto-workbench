/**
 * The Pi half of "which worktree is this agent working in": turning a Pi tool
 * result into the payload ai/bin/report-agent-worktree reads.
 *
 * index.ts imports the Pi SDK, so it only loads inside a Pi session. This file
 * imports node built-ins only, so tests/pi_extensions_agent_worktree.bats can
 * run it under plain `node`.
 *
 * There is no predicate here on purpose. Which worktree a call targeted needs
 * git resolution — toplevel, the `git worktree list` spelling, a `wt switch`
 * branch lookup — and that answer is ai/lib/pr/agent_worktree.py's, reached
 * from both harnesses. This side only reshapes Pi's event into Claude's
 * PostToolUse payload, so one parser reads both.
 */

import { homedir } from "node:os";
import { resolve } from "node:path";

/** The installed hook both harnesses run (ai/bin/report-agent-worktree). */
export const HOOK_PATH = `${homedir()}/.local/bin/report-agent-worktree`;

/** The parts of Pi's `tool_result` event this reads. */
export interface ToolResultEvent {
  toolName: string;
  input?: unknown;
  content?: unknown;
}

/** The Claude-shaped payload the hook reads on stdin. */
export interface HookPayload {
  tool_name: string;
  tool_input: { command?: string; file_path?: string };
  tool_response?: { stdout: string };
  cwd: string;
}

/** The text a tool result carried, flattened. */
export function resultText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => (part && typeof part === "object" && "text" in part
      ? String((part as { text?: unknown }).text ?? "") : ""))
    .join("\n");
}

/** PATH as an absolute path: `~/` from HOME, anything relative from CWD. */
function absolute(path: string, cwd: string): string {
  if (path === "~" || path.startsWith("~/")) {
    return resolve(process.env.HOME ?? "", path.slice(2));
  }
  return resolve(cwd, path);
}

/**
 * EVENT as Claude's PostToolUse payload, or null for a tool that names no path.
 *
 * `bash` keeps its command and output (`wt switch --format json` prints the
 * worktree it made); `edit` and `write` carry Pi's `path` as Claude's
 * absolute `file_path`. CWD is the session's directory — Pi does not keep a
 * `cd` across calls, so it is also the agent's own checkout.
 */
export function payloadFor(event: ToolResultEvent, cwd: string): HookPayload | null {
  const input = (event.input && typeof event.input === "object" ? event.input : {}) as
    { command?: unknown; path?: unknown };
  if (event.toolName === "bash") {
    if (typeof input.command !== "string" || !input.command) return null;
    return {
      tool_name: "Bash",
      tool_input: { command: input.command },
      tool_response: { stdout: resultText(event.content) },
      cwd,
    };
  }
  if (event.toolName === "edit" || event.toolName === "write") {
    if (typeof input.path !== "string" || !input.path) return null;
    return {
      tool_name: event.toolName === "edit" ? "Edit" : "Write",
      tool_input: { file_path: absolute(input.path, cwd) },
      cwd,
    };
  }
  return null;
}

/**
 * True when this session should publish its worktree: an interactive (`tui`)
 * session inside a herdr terminal.
 *
 * json/print are subagents and `pr` pipeline runs, which must not claim the
 * terminal of the session that started them; rpc has no terminal of its own.
 * An empty HC_TERMINAL_ID counts as unset, as it does in the hook.
 */
/**
 * The hook's environment: ENV with CLAUDE_PROJECT_DIR set to CWD. Pi sets no
 * CLAUDE_PROJECT_DIR, and the session cwd is the agent's own checkout, so the
 * hook's prefilter can skip an edit inside it without starting Python.
 */
export function hookEnv(
  env: Record<string, string | undefined>, cwd: string,
): Record<string, string | undefined> {
  return cwd ? { ...env, CLAUDE_PROJECT_DIR: cwd } : env;
}

export function shouldReport(mode: string, env: Record<string, string | undefined>): boolean {
  return mode === "tui" && Boolean(env.HC_TERMINAL_ID);
}
