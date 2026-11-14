/**
 * The run session's file and process edges: reading the run's history,
 * digesting the Now block, and finding `pr`. Node built-ins and ../_shared
 * only, so node loads it in tests. It re-exports the pure modules so wire.ts
 * has one import, and so ../_shared is reached through a sibling of index.ts
 * rather than from it (ai/pi/extensions/README.md).
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export {
  RUN_DIR_ENV,
  SESSION_ID_PREFIX,
  runDirFrom,
  runIdOf,
  runSessionDir,
} from "../_shared/run-dir.ts";
export * from "./events.ts";
export * from "./now.ts";
export * from "./session.ts";

/** The history file in a run directory (ai/lib/core/run_events.py EVENTS_FILE). */
export const EVENTS_FILE = "events.jsonl";

/** ai/bin/pr beside this checkout, from this module's real path. */
const CHECKOUT_PR = fileURLToPath(new URL("../../../bin/pr", import.meta.url));

/**
 * The run's whole history, or null when the run has none (a run older than
 * events.jsonl). Any other failure throws: an unreadable history reported as
 * an absent one would read as "nothing happened".
 */
export function readEvents(runDir: string): Uint8Array | null {
  try {
    return readFileSync(join(runDir, EVENTS_FILE));
  } catch (err) {
    if ((err as { code?: string }).code === "ENOENT") return null;
    throw err;
  }
}

/** sha256 hex of `text`: how a run session tells whether Now changed. */
export function digest(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

/**
 * The `pr` to run: the checkout's ai/bin/pr when it exists, else `pr` on PATH
 * (the extensions running from somewhere without one).
 */
export function prBinary(besideCheckout: string = CHECKOUT_PR): string {
  return existsSync(besideCheckout) ? besideCheckout : "pr";
}
