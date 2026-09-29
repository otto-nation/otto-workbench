/**
 * Recognising a hand-run `gh issue create` and the issue it produced.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file imports only ../_shared, which imports nothing, so
 * tests/pi_extensions.bats can run it under plain `node`.
 *
 * The Claude twin is ai/claude/bin/claude-issue-capture, and the two answer the
 * same question about the same command. This one is the *recording* half of a
 * rule whose refusing half already exists in both harnesses
 * (issue-defer-guard here, `re_issue_create` there) — a filing that the guard
 * lets through is one the ledger should know about.
 *
 * Why this is post-execution and the guard is not: the guard runs before the
 * command, when the issue has no id yet. A refusal needs only the argv; a
 * record needs the answer.
 */

import { statements } from "../_shared/statements.ts";

/**
 * `gh issue create` as the command being run.
 *
 * The same shape ISSUE_CREATE matches in ../issue-defer-guard/detect.ts, and
 * deliberately so: the guard decides whether to refuse this command and this
 * decides whether to record it, and a filing recognised by one and not the
 * other is a gap in exactly the case both exist for.
 */
const ISSUE_CREATE = /^\s*gh\s+issue\s+create(\s|$)/;

/** True when COMMAND files a new issue. */
export function isIssueFiling(command: string): boolean {
  return statements(command).some((statement) => ISSUE_CREATE.test(statement));
}

/**
 * The issue URL `gh issue create` printed, or null.
 *
 * `gh` prints the new issue's URL and nothing else on success, so its absence
 * means the command failed — or printed somewhere this cannot see, which is the
 * same thing here: there is no id to record either way.
 *
 * The last match rather than the first: a command that filed after printing
 * something else leaves the URL at the end, and an earlier one would belong to
 * whatever ran before it.
 */
export function filedIssueUrl(output: string): string | null {
  const matches = output.match(/https:\/\/\S+\/issues\/\d+/g);
  return matches === null ? null : matches[matches.length - 1];
}

/** The issue's own id, from the URL `gh` printed. */
export function issueIdFrom(url: string): string {
  return url.replace(/\/+$/, "").split("/").pop() ?? "";
}
