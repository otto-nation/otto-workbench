/**
 * The run session's handlers, kept out of index.ts so a test can hand them a
 * stand-in `pi`: this file imports SDK types only, which node erases, while
 * index.ts imports pi-tui's Text as a value. What a run session is: the
 * `run-session` section of ai/pi/extensions/README.md.
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import type { Component } from "@earendil-works/pi-tui";
import {
  HISTORY_TYPE,
  NOW_TYPE,
  SECTION_NAME,
  STATE_TYPE,
  consume,
  digest,
  historyText,
  latestDecisions,
  latestState,
  parseReport,
  prBinary,
  readEvents,
  renderNow,
  reportArgv,
  runIdOf,
  runSessionDir,
  systemNote,
  type RunEvent,
  type RunState,
} from "./detect.ts";

export interface Deps {
  /** The environment the session started with; index.ts passes process.env. */
  env: Record<string, string | undefined>;
  /** A TUI block showing `text`; index.ts passes pi-tui's Text. */
  block: (text: string) => Component;
  /** The `pr` to run; detect.ts prBinary() when omitted. */
  pr?: string;
}

/** How long one offline report may take before Now says it is unavailable. */
export const REPORT_TIMEOUT_MS = 30_000;

interface Refreshed {
  state: RunState;
  /** The Now text when it changed since the stored digest, else null. */
  now: string | null;
}

export function wire(pi: ExtensionAPI, deps: Deps): void {
  const pr = deps.pr ?? prBinary();
  // The run this session belongs to; null while it is not a run session.
  let state: RunState | null = null;

  pi.registerEntryRenderer(HISTORY_TYPE, (entry) => {
    const text = (entry.data as { text?: unknown } | undefined)?.text;
    return typeof text === "string" ? deps.block(text) : undefined;
  });

  /**
   * The Now block: the offline report's sessions with their latest decisions,
   * or a paragraph saying why the report could not be read.
   *
   * ceiling: one offline `pr batch report` per turn (a python start-up, about a
   * second); cache it on state.json's and events.jsonl's mtimes once that spawn
   * shows up in turn latency.
   */
  async function nowText(s: RunState, cwd: string, all: RunEvent[]): Promise<string> {
    const argv = reportArgv(s.runId);
    const unavailable = (why: string) =>
      `## Now — run ${s.runId}\nThe report is unavailable: \`pr ${argv.join(" ")}\` ${why}. Run it yourself to see why.`;
    let result;
    try {
      result = await pi.exec(pr, argv, { cwd, timeout: REPORT_TIMEOUT_MS });
    } catch (err) {
      return unavailable(`could not start (${(err as Error).message})`);
    }
    if (result.killed) return unavailable(`was stopped after ${REPORT_TIMEOUT_MS / 1000}s`);
    if (result.code !== 0) {
      const first = result.stderr.trim().split("\n")[0] ?? "";
      return unavailable(`exited ${result.code}${first ? `: ${first}` : ""}`);
    }
    const parsed = parseReport(result.stdout);
    if (!parsed.ok) return unavailable(`printed no usable report (${parsed.error})`);
    return renderNow(s.runId, parsed.sessions, latestDecisions(all));
  }

  /**
   * Bring the session up to date with its run: file the history past the
   * cursor as a TUI-only entry, rebuild Now, and file the advanced cursor and
   * Now's digest as a new workbench-run entry when either moved.
   */
  async function refresh(s: RunState, cwd: string, first: boolean): Promise<Refreshed> {
    let bytes: Uint8Array | null = null;
    let readError = "";
    try {
      bytes = readEvents(s.runDir);
    } catch (err) {
      readError = (err as Error).message;
    }
    const chunk = bytes ? consume(bytes, s.cursor) : { events: [], skipped: 0, cursor: s.cursor };
    let history = "";
    if (readError) {
      history = `Run ${s.runId}: could not read its history (${readError}).`;
    } else if (!bytes) {
      history = first ? `Run ${s.runId}: no history recorded (the run predates events.jsonl).` : "";
    } else {
      const title = first ? `Run ${s.runId} history` : `Run ${s.runId}: new since last look`;
      history = historyText(chunk.events, chunk.skipped, title);
      if (!history && first) history = `Run ${s.runId}: no events recorded yet.`;
    }
    if (history) pi.appendEntry(HISTORY_TYPE, { text: history, runId: s.runId });
    const now = await nowText(s, cwd, bytes ? consume(bytes, 0).events : []);
    const nowDigest = digest(now);
    const next: RunState = { ...s, cursor: chunk.cursor, nowDigest };
    if (next.cursor !== s.cursor || nowDigest !== s.nowDigest) pi.appendEntry(STATE_TYPE, next);
    return { state: next, now: nowDigest === s.nowDigest ? null : now };
  }

  pi.on("session_start", async (_event, ctx: ExtensionContext) => {
    state = null;
    const stored = latestState(ctx.sessionManager.getBranch());
    const runDir = stored ? null : runSessionDir(deps.env, ctx.sessionManager.getSessionId());
    if (!stored && !runDir) return;
    const base: RunState = stored ?? {
      runDir: runDir as string,
      runId: runIdOf(runDir as string),
      cursor: 0,
      nowDigest: "",
    };
    const refreshed = await refresh(base, ctx.cwd, !stored);
    state = refreshed.state;
    // No options: an idle session stores the message without starting a turn.
    if (refreshed.now) pi.sendMessage({ customType: NOW_TYPE, content: refreshed.now, display: true });
  });

  pi.on("before_agent_start", async (event, ctx: ExtensionContext) => {
    if (!state) return;
    const sections = (event.systemPromptOptions.sections ??= {});
    sections[SECTION_NAME] = systemNote(state, ctx.cwd);
    const refreshed = await refresh(state, ctx.cwd, false);
    state = refreshed.state;
    if (!refreshed.now) return;
    return { message: { customType: NOW_TYPE, content: refreshed.now, display: true } };
  });
}
