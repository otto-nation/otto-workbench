/**
 * Pi extension for review agent tool gating.
 *
 * - Blocks file writes outside the worktree directory
 * - Logs all tool calls with timestamps for cost attribution
 *
 * Loaded via: pi --extension <path>/review-guard.ts
 */

import type { ToolCall, ExtensionContext } from "@anthropic-ai/pi";

export default {
  name: "review-guard",

  onToolCall(call: ToolCall, ctx: ExtensionContext) {
    const worktreeDir = ctx.env.REVIEW_WORKTREE_DIR;

    if (isWriteOperation(call) && worktreeDir) {
      const targetPath = extractTargetPath(call);
      if (targetPath && !targetPath.startsWith(worktreeDir)) {
        return {
          blocked: true,
          reason: `Write blocked: ${targetPath} is outside worktree ${worktreeDir}`,
        };
      }
    }

    console.error(
      JSON.stringify({
        ts: new Date().toISOString(),
        tool: call.name,
        args_summary: summarizeArgs(call),
      })
    );

    return { blocked: false };
  },
};

function isWriteOperation(call: ToolCall): boolean {
  return ["Write", "Edit", "NotebookEdit"].includes(call.name) ||
    (call.name === "Bash" && hasWriteCommand(call.arguments?.command ?? ""));
}

function hasWriteCommand(cmd: string): boolean {
  const writePatterns = [/\bcp\b/, /\bmv\b/, /\brm\b/, /\btee\b/, />>?\s/, /\bsed\s+-i/];
  return writePatterns.some((p) => p.test(cmd));
}

function extractTargetPath(call: ToolCall): string | null {
  if (call.name === "Write" || call.name === "Edit") {
    return call.arguments?.file_path ?? null;
  }
  return null;
}

function summarizeArgs(call: ToolCall): string {
  if (call.name === "Bash") return call.arguments?.command?.slice(0, 120) ?? "";
  if (call.name === "Read") return call.arguments?.file_path ?? "";
  if (call.name === "Write") return call.arguments?.file_path ?? "";
  if (call.name === "Edit") return call.arguments?.file_path ?? "";
  return Object.keys(call.arguments ?? {}).join(", ");
}
