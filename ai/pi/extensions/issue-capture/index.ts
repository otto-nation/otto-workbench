/**
 * Records an issue filed by hand in the branch's follow-up ledger.
 *
 * An issue filed through `review.issue.create_issue` records itself, with the
 * source, the reason and both trail ids in hand. One filed by hand — a bare
 * `gh issue create` — goes through none of that, so without this the ledger is
 * a record of what the automation did rather than of what the branch deferred.
 *
 * Claude Code gets the same rule from ai/claude/bin/claude-issue-capture; that
 * hook does not run under Pi.
 *
 * `tool_result` rather than `tool_call`: the issue has no id until the command
 * has run. The guard that *refuses* a filing can work from argv alone and does
 * (issue-defer-guard); recording one cannot.
 *
 * Fails open at every step, and blocks nothing ever. The command has already
 * succeeded by the time this runs — the issue exists whatever happens here, and
 * a hook that complained after the fact would be reporting a failure for work
 * that was done.
 */

import { type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFile } from "node:child_process";
import { filedIssueUrl, isIssueFiling } from "./detect.ts";

/** The text a tool result carried, flattened. */
function resultText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => (part && typeof part === "object" && "text" in part
      ? String((part as { text?: unknown }).text ?? "") : ""))
    .join("\n");
}

export default function (pi: ExtensionAPI) {
  // The command each bash call ran, kept until its result arrives. `tool_result`
  // carries the output and the tool name but not the input that produced it.
  let lastCommand = "";

  pi.on("tool_call", async (event) => {
    if (event.toolName !== "bash") return;
    const input = event.input as { command?: string } | undefined;
    lastCommand = input?.command ?? "";
  });

  pi.on("tool_result", async (event) => {
    if (event.toolName !== "bash" || event.isError) return;
    const command = lastCommand;
    lastCommand = "";
    if (!command || !isIssueFiling(command)) return;

    const url = filedIssueUrl(resultText(event.content));
    if (url === null) return;

    // Handed to ai/bin/record-filed-issue, which owns the write. One
    // implementation of "what a ledger entry for a hand-filed issue looks
    // like", reached from both harnesses rather than written twice — the
    // duplication the guards spent this epic removing. Unawaited: the turn must
    // not wait on a bookkeeping write, and a failure here is not the agent's
    // problem.
    //
    // The payload goes through `child.stdin`, not an `input` option — `execFile`
    // has no such option, so passing one leaves the recorder blocked on a stdin
    // that never closes, and the hook hangs until something kills it. `timeout`
    // is the backstop for a recorder that blocks for any other reason.
    const child = execFile(
      "bash",
      [`${process.env.HOME}/.local/bin/record-filed-issue`],
      { timeout: 10_000 },
      () => {},
    );
    child.stdin?.end(JSON.stringify({
      tool_input: { command },
      tool_response: { stdout: url },
    }));
  });
}
