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
 * The Linear write tools are held to the cross-group rule the same way: their
 * text goes to `owners-check --text`, judged against the checkout's group.
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

/**
 * The Linear write tools (usemaximum/pi-extensions, extensions/linear/tools/)
 * and the parameters of each that carry text Linear will show. Those texts go to
 * Linear, not through a GitHub command, so they are checked with
 * `owners-check --text` against the group of the session's checkout.
 */
const LINEAR_TITLE_BODY = ["title", "description"];
const LINEAR_NAMED = ["name", "description", "content"];
export const LINEAR_TEXT_TOOLS: Record<string, string[]> = {
  linear_create_issue: LINEAR_TITLE_BODY,
  linear_update_issue: LINEAR_TITLE_BODY,
  linear_add_comment: ["body"],
  linear_create_project: LINEAR_NAMED,
  linear_update_project: LINEAR_NAMED,
  linear_create_initiative: LINEAR_NAMED,
  linear_update_initiative: LINEAR_NAMED,
  linear_create_cycle: ["name", "description"],
  linear_update_cycle: ["name", "description"],
  linear_create_release: ["name", "description"],
  linear_update_release: ["name", "description"],
};

/** The text a Linear write tool INPUT would publish, joined for one scan. */
function linearTexts(toolName: string, input: ToolInput): string[] {
  return LINEAR_TEXT_TOOLS[toolName]
    .map((field) => input[field])
    .filter((value): value is string => typeof value === "string" && value !== "");
}

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
  if (Object.hasOwn(LINEAR_TEXT_TOOLS, toolName)) {
    return linearRefusal(linearTexts(toolName, input), cwd, ownersCheck, timeoutMs);
  }
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

/** The refusal for TEXTS a Linear write would publish from CWD, or null. */
function linearRefusal(texts: string[], cwd: string, ownersCheck: string, timeoutMs: number): string | null {
  if (texts.length === 0) return null;
  const dir = mkdtempSync(join(tmpdir(), "upstream-guard-"));
  try {
    const file = join(dir, "text.md");
    writeFileSync(file, texts.join("\n\n"));
    return checkRefusal(
      runOwnersCheck(["--text", file, "--cwd", cwd], ownersCheck, timeoutMs),
      timeoutMs,
      undefined,
      "Linear write",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
