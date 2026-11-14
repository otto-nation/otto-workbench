/**
 * What a run session stores and says: its state entry, the system note, and
 * how record_decision and the Now block call `pr`. Imports nothing, so node
 * loads it in tests.
 */

/** The custom entry holding the run, the history cursor and Now's digest. */
export const STATE_TYPE = "workbench-run";
/** The TUI-only custom entry one history block is filed as. */
export const HISTORY_TYPE = "workbench-run-history";
/** The custom message (model context) the Now block is sent as. */
export const NOW_TYPE = "workbench-run-now";
/** The system-prompt section the note goes in. */
export const SECTION_NAME = "workbench_run";
export const TOOL_NAME = "record_decision";

/** batch.model.Verdict's values; tests/pi_run_session_contract_test.py fails when they differ. */
export const VERDICTS = ["fixed", "dismissed", "deferred", "handed_off"] as const;

/**
 * record_decision's parameters, as plain JSON Schema: pi validates tool
 * arguments as JSON Schema whatever built it, and a literal keeps this module
 * free of imports node cannot resolve.
 */
export const DECISION_SCHEMA = {
  type: "object",
  properties: {
    pr: {
      type: "string",
      description: "The PR as the Now block names it (owner/repo#N), or its number when unique in the run",
    },
    finding: { type: "string", description: "The finding as the Now block names it: <reason>:<step>" },
    verdict: { type: "string", enum: [...VERDICTS], description: "What was decided" },
    reason: { type: "string", minLength: 1, description: "Why, in a sentence" },
    commands: {
      type: "array",
      items: { type: "string" },
      description: "Commands run to act on it, each as typed",
    },
  },
  required: ["pr", "finding", "verdict", "reason"],
  additionalProperties: false,
};

export interface RunState {
  runDir: string;
  runId: string;
  /** Byte offset into events.jsonl just past the history already shown. */
  cursor: number;
  /** sha256 of the last Now block sent, so an unchanged one is not sent again. */
  nowDigest: string;
}

export interface DecisionInput {
  pr: string;
  finding: string;
  verdict: string;
  reason: string;
  commands?: string[];
}

interface EntryLike {
  type: string;
  customType?: string;
  data?: unknown;
}

/** The latest well-formed workbench-run state on `branch` (sessionManager.getBranch()), or null. */
export function latestState(branch: readonly EntryLike[]): RunState | null {
  for (let i = branch.length - 1; i >= 0; i -= 1) {
    const entry = branch[i];
    if (entry.type !== "custom" || entry.customType !== STATE_TYPE) continue;
    const data = entry.data as Partial<RunState> | undefined;
    if (!data || typeof data.runDir !== "string" || typeof data.runId !== "string") continue;
    if (typeof data.cursor !== "number") continue;
    return {
      runDir: data.runDir,
      runId: data.runId,
      cursor: data.cursor,
      nowDigest: typeof data.nowDigest === "string" ? data.nowDigest : "",
    };
  }
  return null;
}

/** `pr` argv for the Now block: the run's report, offline, as JSON. */
export function reportArgv(runId: string): string[] {
  return ["batch", "report", runId, "--offline", "--format", "json"];
}

/**
 * `pr batch note` argv for `input` (ai/lib/cli/pr_batch_session.py
 * add_note_parser). Every flag value is attached with `=`, so a reason or a
 * command starting with `-` is read as that flag's value, not as a flag.
 */
export function noteArgv(runId: string, input: DecisionInput): string[] {
  return [
    "batch",
    "note",
    runId,
    input.pr,
    `--finding=${input.finding}`,
    `--verdict=${input.verdict}`,
    `--reason=${input.reason}`,
    ...(input.commands ?? []).map((command) => `--command=${command}`),
  ];
}

/** The system-prompt section that says what this session is for. */
export function systemNote(state: RunState, cwd: string): string {
  return [
    `You are the orchestrator of pr batch run ${state.runId}. Its checkout is ${cwd}; its run directory is ${state.runDir}.`,
    "- The latest \"Now\" message is the run's report: each PR it waits on, its findings, and the exact commands that act on them. Act only through those commands; never invent flags.",
    `- Record every verdict on a finding, yours or the operator's, with ${TOOL_NAME}. A decision changes no item state: a PR stops waiting only when the report stops showing its finding.`,
    "- Never push, publish, or run a command marked \"pushes\" unless the operator says so in this session.",
    `- The run's full history is ${state.runDir}/events.jsonl, one JSON event per line; read it when the history shown is not enough. Now is read offline; \`pr batch report ${state.runId} --format json\` without --offline asks GitHub too.`,
  ].join("\n");
}
