import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

/**
 * Whether a bash command must be run through otto-queue instead, kept apart
 * from the extension.
 *
 * index.ts imports its event-type helper from the Pi SDK as a value, so only a
 * Pi session can load it. This file pulls in node builtins and nothing else, so
 * a test can run it under plain `node`.
 *
 * The decision and its wording are owned by `bin/local/queue-guard-check`, which
 * Claude Code's claude-bash-guard asks too. Neither harness spells what is
 * heavy, which repos are listed, or the refusal again, so they cannot disagree.
 */

const CHECK = fileURLToPath(
  new URL("../../../../bin/local/queue-guard-check", import.meta.url),
);

// Spelled again in ai/claude/bin/claude-bash-guard, whose tests require the
// citation literally on its block call; tests/queue_guard.bats holds them equal.
const CITATION = "See general.md § Waiting on Background Work.";

/**
 * The refusal for a bash COMMAND run from CWD, or null when it may proceed.
 * Exit 0 from the checker is "refuse" and its stdout is the reason; anything
 * else, including a crash, lets the command run.
 */
export function commandRefusal(command: string, cwd: string): string | null {
  const env = { ...process.env };
  delete env.GIT_DIR;
  delete env.GIT_WORK_TREE;
  delete env.GIT_INDEX_FILE;
  delete env.GIT_COMMON_DIR;
  try {
    const reason = execFileSync(CHECK, [command, cwd], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      env,
    }).trim();
    return reason ? `${reason} ${CITATION}` : null;
  } catch {
    return null;
  }
}
