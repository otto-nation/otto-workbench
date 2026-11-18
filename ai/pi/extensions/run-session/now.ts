/**
 * The session's Now block: `pr batch report` or `pr target report`'s `sessions`,
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
  data?: { commits?: string[] };
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

/** `pr batch report --format json`: `sessions` plus `run.active` from batch.report.build. */
export interface Report {
  sessions: ReportSession[];
  active: boolean;
}

export type Parsed = ({ ok: true } & Report) | { ok: false; error: string };

/** The report's `sessions` and `run.active`, or why there are none to show. */
export function parseReport(stdout: string): Parsed {
  let doc: unknown;
  try {
    doc = JSON.parse(stdout);
  } catch {
    return { ok: false, error: "the report was not JSON" };
  }
  const body = doc as { sessions?: unknown; run?: { active?: unknown } } | null;
  const sessions = body?.sessions;
  if (!Array.isArray(sessions)) return { ok: false, error: "the report has no sessions list" };
  return { ok: true, sessions: sessions as ReportSession[], active: body?.run?.active === true };
}

const SHELL_SAFE = /^[A-Za-z0-9_@%+=:,./#-]+$/;

/** `argv` as one line a shell reads back into the same words. */
export function shellQuote(argv: string[]): string {
  return argv.map((arg) => (SHELL_SAFE.test(arg) ? arg : `'${arg.replaceAll("'", `'\\''`)}'`)).join(" ");
}

/** True when the operator still owes this PR a look. */
function sessionWaiting(s: ReportSession, decisions: Map<string, RunEvent>): boolean {
  if (s.state === "needs_session") return true;
  return (s.findings ?? []).some(
    (f) => !decisions.has(decisionKey(s.pr, `${f.reason}:${f.step}`)),
  );
}

/** The Now block for `subject` (e.g. "run R1" / "target t"): waiting PRs only, each with findings, decisions, commands. */
export function renderNow(
  subject: string,
  sessions: ReportSession[],
  decisions: Map<string, RunEvent>,
  active = false,
): string {
  if (!sessions.length) {
    if (active) {
      return `## Now — ${subject}\nBatch run is still in progress — no settled PRs need attention yet.\n`;
    }
    return `## Now — ${subject}\nNothing in the report: no PR has findings or commands.\n`;
  }
  const waiting = sessions.filter((s) => sessionWaiting(s, decisions));
  const out = [`## Now — ${subject}: ${waiting.length} of ${sessions.length} PRs waiting on you`];
  for (const s of waiting) {
    const base = s.base_ref ? ` → ${s.base_ref}` : "";
    const after = s.after?.length ? ` · after ${s.after.join(", ")}` : "";
    out.push("", `### ${s.pr} — ${s.state} (${s.severity}) · ${s.branch}${base}${after}`);
    for (const f of s.findings ?? []) {
      const key = `${f.reason}:${f.step}`;
      const detail = f.detail ? ` — ${f.detail}` : "";
      const files = f.files?.length ? ` (files: ${f.files.join(", ")})` : "";
      const commits = f.data?.commits?.length ? ` (commits: ${f.data.commits.join(", ")})` : "";
      const log = f.log ? ` log ${f.log}` : "";
      out.push(`- finding \`${key}\`${detail}${files}${commits}${log}`);
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
  const hidden = sessions.length - waiting.length;
  if (hidden) out.push("", `${hidden} settled PR(s) hidden (all findings decided).`);
  return `${out.join("\n")}\n`;
}
