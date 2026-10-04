/**
 * Whether a job poll repeats one this agent run already made, kept apart from
 * the extension.
 *
 * index.ts imports its event-type helper from the Pi SDK as a value, so it can
 * only be loaded from inside a Pi session. This file imports nothing at all, so
 * tests/pi_extensions_job_poll.bats can exercise every branch under plain `node`.
 *
 * What "still running" can honestly mean here. The jobs tools live in another
 * repo (usemaximum/pi-extensions), their JobManager is closure-local, they
 * publish no events, and `tool_call` fires before the tool executes. There is
 * no way to ask whether a job is running at the moment of the call. So the
 * predicate answers a narrower question: has this agent run already polled this
 * target, and did nothing it got back say the job had finished? That is the
 * repeat the rule is about — the second read costs a tool call to learn what
 * the completion message delivers for free.
 */

/**
 * What a poll's result said about the job, as far as the text can be read.
 *
 * `unknown` is not `running`: a line this cannot parse must not be the reason
 * an edit is refused, so the caller treats it the way it treats no result.
 */
export type JobRunStatus = "running" | "done" | "unknown";

export type PollTool = "job_output" | "job_list";

/** One target's history within an agent run. */
type Poll = { calls: number; status: JobRunStatus };

export type PollState = {
  /** Keyed by job id, because polling two jobs is not polling one twice. */
  outputs: Map<string, Poll>;
  /** `job_list` has no id, so it gets its own counter rather than a key. */
  list: Poll;
};

export const REFUSAL_OUTPUT =
  "job_output of a still-running job is a poll. A background job messages you " +
  "when it finishes — do other work, or do nothing and wait. Re-reading " +
  "job_output costs a tool call each time to learn what the completion message " +
  "says once, for free. See general.md § Waiting on Background Work.";

export const REFUSAL_LIST =
  "job_list while a job is still running is a poll. A background job messages " +
  "you when it finishes — do other work, or do nothing and wait. Re-listing " +
  "costs a tool call each time to learn what the completion message says once, " +
  "for free. See general.md § Waiting on Background Work.";

export function freshState(): PollState {
  return { outputs: new Map(), list: { calls: 0, status: "unknown" } };
}

/**
 * Forget this agent run's polls, in place so index.ts can close over one object.
 *
 * Called on `agent_start`, not `turn_start`. A job's completion is delivered as
 * `nextTurn`, so it lands at the start of a later agent run: resetting per turn
 * would only ever catch two polls inside a single assistant message, and would
 * refuse the one legitimate read that follows the completion notice.
 */
export function resetState(state: PollState): void {
  state.outputs.clear();
  state.list = { calls: 0, status: "unknown" };
}

// ceiling: the two parsers below read another repo's rendered text —
// formatJobLine and the job_list header in usemaximum/pi-extensions. Upgrade
// to reading the tool result's `details` once that extension puts the job
// state there, or exposes a listJobs API, so a format change stops being
// invisible. Until then an unparseable line is treated as no answer, which
// fails open rather than refusing on a string that moved.

/**
 * The id and state on a `job_output` first line, or null when it does not parse.
 *
 * The line is `${id}  [${status}]  ${label}`, where status is `running Ns`,
 * `exit C after Ns`, `killed after Ns` or `failed after Ns`.
 */
export function parseOutputStatus(
  text: string,
): { id: string; status: JobRunStatus } | null {
  const match = /^(\S+)\s+\[([^\]]+)\]/.exec(text.trimStart());
  if (!match) return null;
  const [, id, inner] = match;
  if (inner.startsWith("running ")) return { id, status: "running" };
  if (
    inner.startsWith("exit ") ||
    inner.startsWith("killed ") ||
    inner.startsWith("failed ")
  ) {
    return { id, status: "done" };
  }
  return { id, status: "unknown" };
}

/**
 * How many jobs a `job_list` result said were running, or null if unreadable.
 *
 * `No background jobs.` is zero rather than unreadable — an empty list is a
 * real answer, and the next call is not a repeat of anything.
 */
export function parseListRunningCount(text: string): number | null {
  const trimmed = text.trimStart();
  if (trimmed.startsWith("No background jobs")) return 0;
  const match = /^\d+ job\(s\), (\d+) running:/.exec(trimmed);
  return match ? Number(match[1]) : null;
}

/**
 * The refusal for this call, or null to allow it. Does not mutate `state`.
 *
 * The first poll of a target in an agent run is always allowed: it is how the
 * agent learns the job's state, and the rule is about the repeat. After that,
 * a call is allowed only if something already said the work had finished —
 * re-reading a finished job to raise `max_bytes` is what the tool's own
 * description tells you to do.
 *
 * A prior call whose result has not been seen yet is refused. Two polls in one
 * parallel batch reach `tool_call` before either result arrives, and that is
 * the repeat with the least excuse.
 */
export function pollRefusal(
  state: PollState,
  tool: PollTool,
  jobId?: string,
): string | null {
  if (tool === "job_list") {
    if (state.list.calls === 0) return null;
    return state.list.status === "done" ? null : REFUSAL_LIST;
  }
  if (!jobId) return null;
  const prior = state.outputs.get(jobId);
  if (!prior || prior.calls === 0) return null;
  return prior.status === "done" ? null : REFUSAL_OUTPUT;
}

/** Record a call the guard allowed. Its result has not arrived yet. */
export function noteCall(state: PollState, tool: PollTool, jobId?: string): void {
  if (tool === "job_list") {
    state.list = { calls: state.list.calls + 1, status: "unknown" };
    return;
  }
  if (!jobId) return;
  const prior = state.outputs.get(jobId);
  state.outputs.set(jobId, { calls: (prior?.calls ?? 0) + 1, status: "unknown" });
}

/**
 * Record what a poll came back with.
 *
 * An errored result counts as finished. `No such job: x` means the id was
 * wrong, and refusing the corrected retry would be the guard punishing a typo
 * rather than a poll.
 */
export function noteResult(
  state: PollState,
  tool: PollTool,
  text: string,
  isError: boolean,
): void {
  if (tool === "job_list") {
    const running = isError ? 0 : parseListRunningCount(text);
    state.list = {
      calls: Math.max(1, state.list.calls),
      status: running === null ? "unknown" : running > 0 ? "running" : "done",
    };
    return;
  }
  if (isError) {
    // No id to key on when the call failed, so settle every outstanding poll:
    // the alternative is parsing the error text for an id it may not carry.
    for (const [id, poll] of state.outputs) {
      state.outputs.set(id, { calls: poll.calls, status: "done" });
    }
    return;
  }
  const parsed = parseOutputStatus(text);
  if (!parsed) return;
  const prior = state.outputs.get(parsed.id);
  state.outputs.set(parsed.id, {
    calls: Math.max(1, prior?.calls ?? 0),
    status: parsed.status,
  });
}
