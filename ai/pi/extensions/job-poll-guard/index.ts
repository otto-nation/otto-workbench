/**
 * Refuses a second read of a background job this agent run already polled.
 *
 * A job messages the agent when it finishes, so re-reading `job_output` on a
 * timer costs a tool call each time to learn what the completion notice
 * delivers once, for free. `sleep-guard` refuses the same wait spelled as a
 * sleep; this refuses it spelled as a poll. Both enforce one rule written down
 * in general.md § Waiting on Background Work.
 *
 * Pi-only, and not because of an oversight: `job_start` and its siblings are
 * tools the jobs extension registers, and Claude Code has no equivalent for
 * ai/claude/bin/claude-bash-guard to refuse.
 *
 * State resets on `agent_start` rather than `turn_start`. A job's completion
 * arrives as a `nextTurn` message, so it lands at the head of a later agent
 * run: a per-turn reset would only catch two polls inside one assistant
 * message, and would refuse the legitimate read that follows the notice.
 *
 * Installed globally by ai/pi/steps.sh, which is right for this one: it needs
 * no environment set up and has nothing to say about the repository it is in.
 *
 * The predicate lives in ./detect.ts so it can be tested without the SDK — see
 * that file's header, including what "still running" can honestly mean when
 * the jobs tools expose no way to ask.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import {
  freshState,
  noteCall,
  noteResult,
  pollRefusal,
  resetState,
  type PollTool,
} from "./detect.ts";

/** The jobs tools' inputs, which the SDK does not type for us. */
type JobOutputInput = { readonly id: string };
type JobListInput = Record<string, never>;

function resultText(content: readonly { type: string; text?: string }[]): string {
  return content
    .filter((part) => part.type === "text")
    .map((part) => part.text ?? "")
    .join("");
}

export default function (pi: ExtensionAPI) {
  const state = freshState();

  pi.on("agent_start", async () => {
    resetState(state);
  });

  pi.on("tool_call", async (event) => {
    if (isToolCallEventType<"job_output", JobOutputInput>("job_output", event)) {
      const id = event.input.id;
      // No id to key on is a malformed call the tool will reject itself.
      if (!id) return;
      const reason = pollRefusal(state, "job_output", id);
      // terminate is left off deliberately, as in the other guards: the agent
      // should carry on with the turn having skipped the poll, which is the
      // whole point.
      if (reason) return { block: true, reason };
      noteCall(state, "job_output", id);
      return;
    }

    if (isToolCallEventType<"job_list", JobListInput>("job_list", event)) {
      const reason = pollRefusal(state, "job_list");
      if (reason) return { block: true, reason };
      noteCall(state, "job_list");
    }
  });

  pi.on("tool_result", async (event) => {
    if (event.toolName !== "job_output" && event.toolName !== "job_list") return;
    noteResult(
      state,
      event.toolName as PollTool,
      resultText(event.content),
      event.isError,
    );
  });
}
