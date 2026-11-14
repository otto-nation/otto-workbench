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
