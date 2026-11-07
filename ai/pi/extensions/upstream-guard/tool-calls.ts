import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CHECK_TIMEOUT_MS, OWNERS_CHECK, checkRefusal } from "./detect.ts";
import { runOwnersCheck } from "../_shared/github-writes.ts";

/**
 * The owner boundary for Pi tools that publish without going through bash.
 *
 * gh_pr_post_comment, gh_pr_reply_comment and gh_pr_bulk_reply (usemaximum/
 * pi-extensions, extensions/github-pr/tools/pr.ts) post through `pi.exec`, so
 * the bash `tool_call` check never sees them. This module asks the same
 * owners-check about the equivalent `gh api` call run from the session's
 * directory, with every body written to a file so its text is scanned.
 *
 * The tools resolve their target with `gh repo view` in the session's cwd. The
 * check cannot know which remote that is, so it substitutes the stricter rule
 * the bash guard applies to an un-flagged `gh pr comment`: every remote at the
 * cwd must be in one group, and every body must stay inside it.
 *
 * ceiling: the tool names are a list. A publishing tool added upstream under
 * another name is unguarded until it is listed here. Upgrade trigger: when a
 * new publishing tool appears in usemaximum/pi-extensions.
 *
 * Imports only Node built-ins and sibling modules, never the Pi SDK, so node
 * can load it in tests/pi_extensions_upstream.bats.
 */

type ToolInput = Record<string, unknown>;

/** Each publishing tool's way to the texts it would post. */
export const PUBLISHING_TOOLS: Record<string, (input: ToolInput) => string[]> = {
  gh_pr_post_comment: (input) => [String(input.body ?? "")],
  gh_pr_reply_comment: (input) => [String(input.body ?? "")],
  gh_pr_bulk_reply: (input) =>
    (Array.isArray(input.replies) ? (input.replies as ToolInput[]) : []).map((reply) =>
      String(reply?.body ?? ""),
    ),
};

/** WORD as one shell word. */
export function quote(word: string): string {
  return `'${word.replace(/'/g, `'\\''`)}'`;
}

/** The refusal for TOOLNAME called with INPUT from CWD, or null when it may run. */
export function toolCallRefusal(
  toolName: string,
  input: ToolInput,
  cwd: string,
  ownersCheck: string = OWNERS_CHECK,
  timeoutMs: number = CHECK_TIMEOUT_MS,
): string | null {
  const texts = Object.hasOwn(PUBLISHING_TOOLS, toolName) ? PUBLISHING_TOOLS[toolName](input) : [];
  if (texts.length === 0) return null;
  const dir = mkdtempSync(join(tmpdir(), "upstream-guard-"));
  try {
    const words = ["gh", "api", "repos/{owner}/{repo}/issues/comments"];
    texts.forEach((text, i) => {
      const file = join(dir, `body-${i}.md`);
      writeFileSync(file, text);
      words.push("-F", `body${i}=@${file}`);
    });
    const command = words.map(quote).join(" ");
    return checkRefusal(runOwnersCheck(["--cwd", cwd, "--", command], ownersCheck, timeoutMs), timeoutMs);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
