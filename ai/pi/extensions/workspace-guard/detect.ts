import { isAbsolute, relative, resolve } from "node:path";
import { gitRootFor, nearestExistingDir, workspaceFor } from "../_shared/workspace.ts";

// The repo and workspace lookups live in _shared/workspace.ts, which the model
// router reads too; re-exported so this module's callers and tests are unchanged.
export { gitRootFor, nearestExistingDir, workspaceFor };

/**
 * Whether a write is a plan or spec landing in a checkout, kept apart from the
 * extension.
 *
 * index.ts imports its event-type helper from the Pi SDK as a value, so it can
 * only be loaded from inside a Pi session. This file pulls in node builtins and
 * nothing else, so tests/resolve_workspace.bats can run it under plain `node` —
 * which matters most for the probe below, the half that would otherwise break
 * silently: a renamed binary or a changed layout makes it answer "fine" forever,
 * and a guard that has stopped working looks exactly like a guard with nothing
 * to say.
 *
 * Claude Code applies the same rule from ai/claude/bin/claude-edit-guard, and
 * both ask `bin/resolve-workspace` where the artifact should have gone. One
 * script owns that path, so the two harnesses cannot name different locations —
 * which is the entire point, since a guard that refuses a write while pointing
 * somewhere the other harness disagrees with is worse than no guard.
 */

/**
 * Worktree-relative path prefixes that name a plan or a spec.
 *
 * `workspace/` is the correct home written in the wrong place; the two
 * `ignore/` entries are the convention this replaced; `docs/superpowers/` is
 * where the vendored brainstorming skill writes by default, and is the reason
 * a guard is needed at all — the skill states that path in its own prose, and
 * a rule can only ask it to prefer another.
 *
 * Matched on whole segments rather than as substrings, so `my-workspace/` and
 * `ignoreme/` are ordinary files.
 */
const PLAN_PREFIXES = [
  "workspace",
  "ignore/plans",
  "ignore/specs",
  "docs/superpowers",
];

/** Whether REL, a worktree-relative path, names a plan or a spec. */
export function isPlanPath(rel: string): boolean {
  if (rel.startsWith("..") || isAbsolute(rel)) return false;
  return PLAN_PREFIXES.some(
    (prefix) => rel === prefix || rel.startsWith(prefix + "/"),
  );
}

export function refusalFor(workspace: string): string {
  if (workspace) {
    return (
      "A plan or spec belongs to the repository, not to this branch. Written " +
      "here it is duplicated across every sibling worktree, invisible from " +
      "them, and deleted by the `wt remove` that retires the branch — while " +
      "the work it explains lives on in main. Write it under " +
      `${workspace} instead (plans/ or specs/), which is outside every checkout.`
    );
  }
  return (
    "A plan or spec belongs at the repository's container, and this is an " +
    "ordinary clone — it has no container, so there is nowhere correct to put " +
    "one. Convert the repo with `wt-init`, then write it under the path " +
    "`resolve-workspace` prints. Writing it inside the checkout is the " +
    "arrangement this refuses."
  );
}

/**
 * The refusal for a write to PATH, or null when it may proceed.
 *
 * Fails open when there is no git and when the path is not a plan or spec. It
 * does *not* fail open when the workspace cannot be resolved: that is the
 * ordinary-clone case, which the rule refuses outright rather than letting the
 * artifact land inside the checkout.
 */
export function workspaceRefusal(path: string): string | null {
  const abs = resolve(path);
  const tree = gitRootFor(abs);
  if (!tree) return null;

  const rel = relative(resolve(tree), abs);
  if (!isPlanPath(rel)) return null;

  return refusalFor(workspaceFor(tree));
}
