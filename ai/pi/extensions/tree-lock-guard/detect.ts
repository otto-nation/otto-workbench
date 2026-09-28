import { execFileSync } from "node:child_process";
import { dirname } from "node:path";

/**
 * Whether a tree is being validated right now, kept apart from the extension.
 *
 * index.ts imports its event-type helper from the Pi SDK as a value, so it can
 * only be loaded from inside a Pi session. This file pulls in two node
 * builtins and nothing else, so tests/pi_extensions.bats can run it under
 * plain `node` — which matters most for the probe below, the half that would
 * otherwise break silently: a renamed binary or a moved lock path makes it
 * answer "free" forever, and a guard that has stopped working looks exactly
 * like a guard with nothing to say.
 *
 * The lock itself is ai/lib/core/tree_lock.py, held LOCK_SH by every validator
 * (bin/local/run-tests, bin/local/validate-all, and the Taskfile test targets
 * routed through run-tests). Claude Code reads the same fact through
 * ai/claude/bin/claude-edit-guard. One lock, two readers, so the two harnesses
 * cannot disagree about whether a tree is under validation.
 */

/** Where `with-tree-lock` lives, resolved from this file rather than $PATH. */
const WITH_TREE_LOCK = new URL(
  "../../../../bin/local/with-tree-lock",
  import.meta.url,
).pathname;

export const REFUSAL =
  "A validator holds this tree — a suite or a gate is reading the files you are " +
  "about to change. Editing now invalidates that run: it reports against a tree " +
  "that no longer exists, and a green result would be exactly as green. Wait for " +
  "it to finish, or make the edit in another worktree.";

/**
 * The repository working tree containing PATH, or "" when there is none.
 *
 * Resolved from the edited file rather than from the process's cwd, because a
 * Pi session's cwd is not necessarily the tree being written to — an agent
 * editing across worktrees would otherwise probe the wrong lock and be told a
 * busy tree is free.
 *
 * GIT_DIR and friends are stripped first. Git skips discovery entirely when
 * GIT_DIR is set, so with one inherited from a hook the `-C` below is ignored
 * and the answer is about the hook's repository — a wrong answer arriving as a
 * success. GIT_COMMON_DIR goes with them: it redirects the shared storage a
 * linked worktree reads, and the Claude twin strips the same four.
 */
export function gitRootFor(path: string): string {
  const env = { ...process.env };
  delete env.GIT_DIR;
  delete env.GIT_WORK_TREE;
  delete env.GIT_INDEX_FILE;
  delete env.GIT_COMMON_DIR;

  try {
    return execFileSync("git", ["-C", dirname(path), "rev-parse", "--show-toplevel"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      env,
    }).trim();
  } catch {
    return "";
  }
}

/**
 * True when something holds the validation lock on TREE.
 *
 * Shells to `with-tree-lock --check`, which is `tree_lock.is_locked()`, rather
 * than reimplementing the probe: node has no `fcntl.flock`, and a second
 * implementation of the one fact both harnesses read is the way they come to
 * disagree. Exit 0 means a validator holds it, 1 means free.
 *
 * ceiling: one `git` spawn plus one `python3` spawn per Edit/Write call,
 * measured at well under the 50ms an Edit costs elsewhere. Upgrade to an
 * in-process flock probe if a measurement puts this over 50ms, or if Pi grows
 * a way to keep a helper process alive across tool calls.
 */
export function treeIsLocked(tree: string): boolean {
  try {
    execFileSync(WITH_TREE_LOCK, ["--check", tree], {
      stdio: ["ignore", "ignore", "ignore"],
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * The refusal for an edit to PATH, or null when the edit may proceed.
 *
 * Fails open at every step — no git, no tree, no lock, or a probe that cannot
 * run all mean silence. A guard that refused because it could not answer would
 * block every edit on the machine the moment the binary moved.
 *
 * WORKBENCH_TREE_LOCK is deliberately not read. That variable is the writers'
 * reentrancy marker, set so a validator re-execing under the wrapper does not
 * deadlock against itself; it says nothing about whether the tree an agent is
 * editing is under validation, and honouring it here would let any process
 * that inherited it edit straight through the lock.
 */
export function lockRefusal(path: string): string | null {
  const tree = gitRootFor(path);
  if (!tree) return null;
  if (!treeIsLocked(tree)) return null;

  // No pid: holders() can be empty while the lock is held — a record torn
  // mid-write, or a holder that never wrote one — so naming one would be a
  // detail the guard cannot stand behind.
  return REFUSAL;
}
