/**
 * Refuses a bash `timeout` that is in the wrong unit, or that would SIGKILL a
 * git write or a test run part-way.
 *
 * Pi's bash tool takes `timeout` in seconds and, when it fires, SIGKILLs the
 * command's whole process group at once. Two habits met that badly: agents
 * passed milliseconds (`15000` — four hours, so no bound at all), and they put
 * 10–30 second bounds on git and test commands, which the kill then cut off
 * mid-write, stranding `packed-refs.lock` and `index.lock`. The rule is in
 * general.md § Waiting on Background Work; the predicate and the evidence are
 * in ./detect.ts.
 *
 * Installed globally by ai/pi/steps.sh, alongside sleep-guard and
 * test-pipe-guard: it needs no environment and is not specific to any repo.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import {
  MAX_SECONDS,
  MIN_SECONDS_FOR_WRITERS,
  TimeoutProblem,
  timeoutFinding,
} from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    if (!isToolCallEventType("bash", event)) return;

    const timeout = event.input.timeout;
    const finding = timeoutFinding(event.input.command, timeout);
    if (finding === null) return;

    // terminate is left off, matching the other guards: the agent should carry
    // on with a rewritten call, not lose the turn to the refusal.
    if (finding.problem === TimeoutProblem.TooLong) {
      return {
        block: true,
        reason:
          `The bash tool's \`timeout\` is in seconds, so ${timeout} is ` +
          `${(timeout! / 3600).toFixed(1)} hours — a millisecond value passed as seconds, ` +
          `or a bound past ${MAX_SECONDS}s that belongs in job_start (which reports its ` +
          `own completion). Pass seconds, or drop the timeout.`,
      };
    }
    return {
      block: true,
      reason:
        `A ${timeout}s bash \`timeout\` on \`${finding.writer}\` is refused: when it fires, ` +
        `Pi SIGKILLs the whole process group with no SIGTERM first, and a git write cut ` +
        `off that way strands its \`*.lock\` for every later command in the repo. Drop the ` +
        `timeout, or bound the command the gentle way — \`timeout <seconds> ${finding.writer} ...\` ` +
        `inside the command (SIGTERM first), or job_start with timeout_minutes — and keep ` +
        `any tool timeout of at least ${MIN_SECONDS_FOR_WRITERS}s as a backstop above it. ` +
        `See general.md § Waiting on Background Work.`,
    };
  });
}
