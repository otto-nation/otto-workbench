/**
 * The run session's Now block: `pr batch report --format json`'s `sessions`,
 * each finding with its latest operator decision. Never rebuilt from the
 * history — the report is the only source of what a PR waits on. Imports only
 * ./events.ts, so node loads it in tests.
 */

import { decisionKey, type RunEvent } from "./events.ts";

export interface ReportFinding {
  reason: string;
  step: string;
  detail?: string;
  files?: string[];
  log?: string | null;
}

export interface ReportCommand {
  argv: string[];
  why?: string;
  action?: string;
  push?: boolean;
}

/** One entry of the report's `sessions` (batch.session_report.SessionEntry.to_dict). */
export interface ReportSession {
  pr: string;
  state: string;
  severity: string;
  branch: string;
  base_ref: string;
  after?: string[];
  findings?: ReportFinding[];
  commands?: ReportCommand[];
  notes?: string[];
}

export type Parsed = { ok: true; sessions: ReportSession[] } | { ok: false; error: string };

/** The report's `sessions`, or why there are none to show. */
export function parseReport(stdout: string): Parsed {
  let doc: unknown;
  try {
    doc = JSON.parse(stdout);
  } catch {
    return { ok: false, error: "the report was not JSON" };
  }
  const sessions = (doc as { sessions?: unknown } | null)?.sessions;
  if (!Array.isArray(sessions)) return { ok: false, error: "the report has no sessions list" };
  return { ok: true, sessions: sessions as ReportSession[] };
}

const SHELL_SAFE = /^[A-Za-z0-9_@%+=:,./#-]+$/;

/** `argv` as one line a shell reads back into the same words. */
export function shellQuote(argv: string[]): string {
  return argv.map((arg) => (SHELL_SAFE.test(arg) ? arg : `'${arg.replaceAll("'", `'\\''`)}'`)).join(" ");
}

/** The Now block for run `runId`: each PR, its findings with their latest decision, its commands. */
export function renderNow(
  runId: string,
  sessions: ReportSession[],
  decisions: Map<string, RunEvent>,
): string {
  if (!sessions.length) {
    return `## Now — run ${runId}\nNothing in the report: no PR has findings or commands.`;
  }
  const waiting = sessions.filter((s) => s.state === "needs_session").length;
  const out = [`## Now — run ${runId}: ${waiting} of ${sessions.length} PRs waiting on you`];
  for (const s of sessions) {
    const base = s.base_ref ? ` → ${s.base_ref}` : "";
    const after = s.after?.length ? ` · after ${s.after.join(", ")}` : "";
    out.push("", `### ${s.pr} — ${s.state} (${s.severity}) · ${s.branch}${base}${after}`);
    for (const f of s.findings ?? []) {
      const key = `${f.reason}:${f.step}`;
      const detail = f.detail ? ` — ${f.detail}` : "";
      const files = f.files?.length ? ` (files: ${f.files.join(", ")})` : "";
      const log = f.log ? ` log ${f.log}` : "";
      out.push(`- finding \`${key}\`${detail}${files}${log}`);
      const decided = decisions.get(decisionKey(s.pr, key));
      if (decided) out.push(`  decided: ${String(decided.verdict)} — ${String(decided.reason)}`);
    }
    for (const c of s.commands ?? []) {
      const why = c.why ? ` — ${c.why}` : "";
      const push = c.push ? " (pushes: only when the operator says so)" : "";
      out.push(`- run \`${shellQuote(c.argv)}\`${why}${push}`);
    }
    for (const note of s.notes ?? []) out.push(`- note: ${note}`);
  }
  return out.join("\n");
}
