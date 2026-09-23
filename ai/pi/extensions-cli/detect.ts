/**
 * The write-capable-command predicate for review-guard, kept apart from the
 * extension that uses it.
 *
 * Same split as the guards under ai/pi/extensions/: review-guard.ts imports
 * `isToolCallEventType` from the Pi SDK as a value, so it only loads inside a
 * Pi session. This file imports nothing but ../extensions/_shared/statements.ts,
 * which itself imports nothing, so tests/pi_extensions.bats can run it under
 * plain `node` and assert the shapes it does and does not match.
 */

import { statements } from "../extensions/_shared/statements.ts";

/**
 * Commands that write, matched at a statement head.
 *
 * Anchored rather than searched for anywhere in the command, because `\b`
 * treats `/` and `-` as word boundaries: an unanchored /\brm\b/ matches the
 * *path* in `cd /repo/isaac-fix-rm-stale && pytest`, and `\binstall\b` matches
 * any path with an `install/` segment. That disabled bash for whole review
 * sessions on nothing but a branch name. A write verb is only a write when it
 * is the command being run.
 */
const WRITE_COMMANDS = [
  "cp",
  "mv",
  "rm",
  "tee",
  "dd",
  "truncate",
  "install",
];

/**
 * Write constructs that are not a bare command name, matched against each
 * statement rather than the whole command string.
 */
const WRITE_STATEMENT_PATTERNS = [
  /^\s*sed\s+[^|]*-i/,
  /^\s*perl\s+[^|]*-i/,
  /^\s*(?:curl|wget)\b[^|]*\s-[a-zA-Z]*[oO]\b/,
  // `apply` and `am` write arbitrary file content straight out of a patch,
  // which is the shape a fix pass reaches for when it wants a diff on disk.
  /^\s*git\s+(?:commit|push|checkout|switch|restore|reset|clean|stash|rebase|merge|apply|am|cherry-pick|revert)\b/,
];

/**
 * A redirect that names a destination, which `2>&1` and `2>/dev/null` do not.
 *
 * Matching a bare `>` instead caught every `cmd 2>&1` an agent writes while
 * reading, and a guard that fires on ordinary reads is one whose refusals stop
 * being read.
 */
const REDIRECT = />>?\s*(?!&\d)(?!\/dev\/(?:null|stdout|stderr)\b)(\S+)/;

/**
 * Scratch destinations a redirect may target.
 *
 * The same list ai/claude/bin/claude-bash-guard exempts, for the same reason,
 * and the two must stay in step: test-pipe-guard refuses a piped suite and
 * prescribes `pytest ... > /tmp/out.txt 2>&1` as the fix. Without this, that
 * prescribed rewrite was itself refused here, and a review agent that obeyed
 * the first refusal could not satisfy the second. Two guards whose remedies
 * contradict each other leave no command the agent can run, which is how a
 * review burned two full turn budgets and wrote nothing.
 */
const SCRATCH_PREFIXES = ["/tmp/", "/private/tmp/", "/var/folders/"];

function isScratchTarget(target: string): boolean {
  const cleaned = target.replace(/^['"]|['"]$/g, "");
  if (cleaned === "/dev/null") return true;
  return SCRATCH_PREFIXES.some((prefix) => cleaned.startsWith(prefix));
}

/** The leading command word of a statement, with env assignments skipped. */
function commandHead(statement: string): string {
  const words = statement.trim().split(/\s+/).filter(Boolean);
  for (const word of words) {
    // `FOO=bar cmd` — an assignment prefix is not the command being run.
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(word)) continue;
    // Strip a path so `/bin/rm` and `rm` read the same.
    return word.split("/").pop() ?? "";
  }
  return "";
}

/**
 * Why `command` may not run in a review session, or null when it may.
 *
 * Returns the offending statement so the refusal can name it, rather than
 * echoing a 120-character slice of the whole command — a truncated summary hid
 * the trailing redirect that was the actual match, and the refusal read as
 * though it had blocked the `cd` in front of it.
 */
export function blockedWriteCommand(command: string): string | null {
  for (const statement of statements(command)) {
    if (!statement.trim()) continue;

    const head = commandHead(statement);
    if (WRITE_COMMANDS.includes(head)) {
      return `\`${head}\` writes: ${statement.trim()}`;
    }

    for (const pattern of WRITE_STATEMENT_PATTERNS) {
      if (pattern.test(statement)) return `write-capable command: ${statement.trim()}`;
    }

    const redirect = REDIRECT.exec(statement);
    if (redirect && !isScratchTarget(redirect[1])) {
      return `redirect writes to ${redirect[1]}: ${statement.trim()}`;
    }
  }
  return null;
}
