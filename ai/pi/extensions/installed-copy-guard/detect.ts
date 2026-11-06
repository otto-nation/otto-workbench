import { execFileSync } from "node:child_process";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Whether a tool call touches the installed copy of the workbench, kept apart
 * from the extension.
 *
 * index.ts imports its event-type helper from the Pi SDK as a value, so only a
 * Pi session can load it. This file pulls in node builtins and nothing else, so
 * a test can run it under plain `node`.
 *
 * The fact itself — "this directory is in the installed copy's repository" —
 * is owned by `bin/local/installed-copy-check`, which Claude Code's
 * claude-edit-guard and claude-bash-guard ask too. Neither harness spells it
 * again, so they cannot disagree.
 */

const CHECK = fileURLToPath(
  new URL("../../../../bin/local/installed-copy-check", import.meta.url),
);

export const EDIT_REFUSAL =
  "This is the installed copy of the workbench (or a worktree of it), which " +
  "`otto-workbench sync` runs from and only sync updates. Change the code in " +
  "the repository's container, ~/git/otto-nation/otto-workbench, in a " +
  "worktree made there with `wt switch -c`, and let sync carry it here. See git-operations.md § The Installed Copy Is Read-Only.";

export const WORKTREE_REFUSAL =
  "Creating a worktree from the installed copy leaves the branch's checkout " +
  "beside the install, outside the container every other worktree lives in. " +
  "Run `wt switch -c` from a worktree in the container, " +
  "~/git/otto-nation/otto-workbench, instead. See git-operations.md § The Installed Copy Is Read-Only.";

/** Exit 0 from the checker is "yes"; anything else, including a crash, is "no". */
function asks(args: string[]): boolean {
  const env = { ...process.env };
  delete env.GIT_DIR;
  delete env.GIT_WORK_TREE;
  delete env.GIT_INDEX_FILE;
  delete env.GIT_COMMON_DIR;
  try {
    execFileSync(CHECK, args, { stdio: "ignore", env });
    return true;
  } catch {
    return false;
  }
}

/** The refusal for an edit or write to PATH, or null when it may proceed. */
export function editRefusal(path: string, cwd: string): string | null {
  const abs = isAbsolute(path) ? path : resolve(cwd, path);
  return asks([abs]) ? EDIT_REFUSAL : null;
}

/** The refusal for a bash COMMAND run from CWD, or null when it may proceed. */
export function commandRefusal(command: string, cwd: string): string | null {
  return asks(["--creating", command, cwd]) ? WORKTREE_REFUSAL : null;
}
