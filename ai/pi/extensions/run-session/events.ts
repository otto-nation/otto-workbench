/**
 * A batch run's history (`<run-dir>/events.jsonl`) as the run session reads it.
 *
 * The file's only writer is ai/lib/core/run_events.py; this reads it the same
 * way: a torn line, a non-object, or one with no string `kind` is skipped and
 * counted, and an unknown kind is kept, so a history written by a newer build
 * still reads. Imports nothing, so node loads it in tests.
 */

export interface RunEvent {
  kind: string;
  at?: string;
  item?: string;
  [field: string]: unknown;
}

export interface Chunk {
  /** Events past the cursor, in file order. */
  events: RunEvent[];
  /** Lines past the cursor that were not a readable event. */
  skipped: number;
  /** Byte offset just past the last complete line read: the next cursor. */
  cursor: number;
}

const NEWLINE = 0x0a;

/**
 * The complete lines of `bytes` (the whole file) past byte offset `cursor`.
 *
 * A trailing line with no newline is still being written, so it is left for
 * the next read and the cursor never moves past half a line. A file shorter
 * than the cursor was replaced rather than appended to, and is read again
 * from the start.
 */
export function consume(bytes: Uint8Array, cursor: number): Chunk {
  const start = cursor > bytes.length ? 0 : Math.max(0, cursor);
  const last = bytes.lastIndexOf(NEWLINE);
  if (last < start) return { events: [], skipped: 0, cursor: start };
  const text = new TextDecoder().decode(bytes.subarray(start, last + 1));
  const events: RunEvent[] = [];
  let skipped = 0;
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      skipped += 1;
      continue;
    }
    const isEvent =
      parsed !== null &&
      typeof parsed === "object" &&
      !Array.isArray(parsed) &&
      typeof (parsed as { kind?: unknown }).kind === "string";
    if (!isEvent) {
      skipped += 1;
      continue;
    }
    events.push(parsed as RunEvent);
  }
  return { events, skipped, cursor: last + 1 };
}

/** The key a decision is filed under: its item and finding (`<reason>:<step>`). */
export function decisionKey(item: string, finding: string): string {
  return `${item}\u0000${finding}`;
}

/**
 * The last `decision` per (item, finding) in `events`, read in file order.
 * Mirrors batch.events.latest_decisions; tests/pi_run_session_contract_test.py
 * holds the two to the same answer over the same file.
 */
export function latestDecisions(events: RunEvent[]): Map<string, RunEvent> {
  const latest = new Map<string, RunEvent>();
  for (const event of events) {
    if (event.kind !== "decision") continue;
    if (typeof event.item !== "string" || typeof event.finding !== "string") continue;
    latest.set(decisionKey(event.item, event.finding), event);
  }
  return latest;
}

/** History lines kept per PR in one block; older lines fold into a count. */
export const HISTORY_CAP = 12;

/** Envelope fields a generic line leaves out. */
const ENVELOPE = new Set(["schema_version", "kind", "at", "run", "item"]);

function str(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  return JSON.stringify(value);
}

function list(value: unknown): string {
  return Array.isArray(value) && value.length ? value.map(str).join(",") : "none";
}

function counts(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  return Object.entries(value as Record<string, unknown>)
    .map(([key, n]) => `${key} ${str(n)}`)
    .join(", ");
}

/** HH:MM of an event's `at` (UTC, as batch.store.now_iso writes it), or --:--. */
function clock(event: RunEvent): string {
  return typeof event.at === "string" && event.at.length >= 16 ? event.at.slice(11, 16) : "--:--";
}

/** One event as one history line, or null for one the history leaves out (`step_log`). */
export function eventLine(e: RunEvent): string | null {
  switch (e.kind) {
    case "run_started":
      return `run started: steps ${list(e.steps)} · pool ${str(e.pool)} · AI steps ${list(e.with_ai)} · rebase ${str(e.rebase)}`;
    case "run_finished":
      return `run finished: ${str(e.status)}`;
    case "run_summary":
      return `run summary: ${counts(e.counts)}`;
    case "cancel_requested":
      return e.kill ? "cancel requested (kill)" : "cancel requested";
    case "item_queued":
      return `queued: steps ${list(e.steps)}`;
    case "admission_wait":
      return `waiting to start ${str(e.step)}: ${str(e.reason)}`;
    case "step_started":
      return `${str(e.step)} started`;
    case "step_finished": {
      const exit = e.exit_code === undefined || e.exit_code === null ? "" : ` (exit ${str(e.exit_code)})`;
      const log = e.log_path ? ` log ${str(e.log_path)}` : "";
      return `${str(e.step)} ${str(e.status)}${exit}${log}`;
    }
    case "step_log":
      return null;
    case "commits_unrecorded":
      return `${str(e.step)}: git would not list its commits; kept tip ${str(e.head)}`;
    case "generated_dirt_restored":
      return `restored generated files: ${list(e.paths)}`;
    case "finding_created":
      return `finding ${str(e.reason)}:${str(e.step)}${e.detail ? ` — ${str(e.detail)}` : ""}`;
    case "item_finished":
      return `finished: ${str(e.status)}`;
    case "decision": {
      const ran = Array.isArray(e.commands) && e.commands.length ? ` (ran: ${e.commands.map(str).join("; ")})` : "";
      return `decided ${str(e.finding)}: ${str(e.verdict)} — ${str(e.reason)}${ran}`;
    }
    case "plan_written":
      return `plan for ${str(e.step)}: ${str(e.path)}`;
    default: {
      const rest = Object.fromEntries(Object.entries(e).filter(([key]) => !ENVELOPE.has(key)));
      return `${e.kind} ${JSON.stringify(rest)}`;
    }
  }
}

/**
 * `events` as a per-PR timeline under `title`: run-level events first, then
 * each PR's lines in the order the PRs first appear. A step's `started` line is
 * replaced by its `finished` line, so one step reads as one line. Each PR keeps
 * its last `cap` lines; the rest fold into a count. Empty when there is
 * nothing to show and nothing was skipped.
 */
export function historyText(
  events: RunEvent[],
  skipped: number,
  title: string,
  cap: number = HISTORY_CAP,
): string {
  const run: string[] = [];
  const items = new Map<string, string[]>();
  const started = new Map<string, number>();
  let shown = 0;
  for (const event of events) {
    const line = eventLine(event);
    if (line === null) continue;
    shown += 1;
    const stamped = `${clock(event)} ${line}`;
    if (typeof event.item !== "string") {
      run.push(stamped);
      continue;
    }
    const lines = items.get(event.item) ?? [];
    items.set(event.item, lines);
    const key = `${event.item}\u0000${str(event.step)}`;
    const open = started.get(key);
    if (event.kind === "step_finished" && open !== undefined) {
      lines[open] = stamped;
      started.delete(key);
      continue;
    }
    if (event.kind === "step_started") started.set(key, lines.length);
    lines.push(stamped);
  }
  if (!shown && !skipped) return "";
  const out = [`${title} (${shown} events)`];
  for (const line of run) out.push(`- ${line}`);
  for (const [item, lines] of items) {
    out.push(item);
    if (lines.length > cap) out.push(`  … ${lines.length - cap} earlier`);
    for (const line of lines.slice(-cap)) out.push(`  - ${line}`);
  }
  if (skipped) out.push(`(${skipped} unreadable line${skipped === 1 ? "" : "s"} skipped)`);
  return out.join("\n");
}
