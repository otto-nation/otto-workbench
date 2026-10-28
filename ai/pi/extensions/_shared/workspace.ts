import { execFileSync } from "node:child_process";
import { dirname } from "node:path";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

/**
 * Which repository a path belongs to, and where that repository's workspace is.
 *
 * Shared by workspace-guard, which refuses a plan written into a checkout, and
 * model-router, which needs to tell an edit to a plan from an edit to the
 * code. Both ask `bin/resolve-workspace`, the one script that owns the path, so
 * neither can name a different location than the other or than Claude Code's
 * claude-edit-guard. Node builtins only, so a test can load it under `node`.
 */

/** Where `resolve-workspace` lives, resolved from this file rather than $PATH. */
const RESOLVE_WORKSPACE = fileURLToPath(
  new URL("../../../../bin/resolve-workspace", import.meta.url),
);

/**
 * The closest ancestor of PATH that exists.
 *
 * A write creating a file in a directory that is not there yet is the ordinary
 * case, not an edge one: the first spec a repo gets creates `specs/` with it.
 * Resolving the repository from the file's own parent fails for every such
 * write and the guard then says nothing — failing open on precisely the writes
 * it exists to catch.
 */
export function nearestExistingDir(path: string): string {
  let dir = dirname(path);
  while (dir !== "/" && dir !== "." && !existsSync(dir)) dir = dirname(dir);
  return dir;
}

/**
 * The repository working tree containing PATH, or "" when there is none.
 *
 * "" is the answer for the container itself, which is a bare repo with no
 * working tree — and that is what lets a write to the correct location
 * through. Resolved from the written file rather than the process cwd,
 * because a Pi session's cwd is not necessarily the tree being written to.
 *
 * GIT_DIR and friends are stripped first. Git skips discovery entirely when
 * GIT_DIR is set, so with one inherited from a hook the `-C` below is ignored
 * and the answer is about the hook's repository — a wrong answer arriving as a
 * success. The Claude twin strips the same four.
 */
export function gitRootFor(path: string): string {
  const env = { ...process.env };
  delete env.GIT_DIR;
  delete env.GIT_WORK_TREE;
  delete env.GIT_INDEX_FILE;
  delete env.GIT_COMMON_DIR;

  try {
    return execFileSync(
      "git",
      ["-C", nearestExistingDir(path), "rev-parse", "--show-toplevel"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], env },
    ).trim();
  } catch {
    return "";
  }
}

/** The workspace for the repo at TREE, or "" when it is an ordinary clone. */
export function workspaceFor(tree: string): string {
  try {
    return execFileSync(RESOLVE_WORKSPACE, [tree], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "";
  }
}
