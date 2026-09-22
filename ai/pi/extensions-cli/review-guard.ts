/**
 * Tool gating for the Pi review and fix agents.
 *
 * Blocks writes outside the review worktree, and logs every tool call as JSON
 * on stderr for cost attribution.
 *
 * Loaded per-invocation by ai/lib/agent/backend_pi.py:
 *
 *     pi --extension <repo>/ai/pi/extensions-cli/review-guard.ts
 *
 * Deliberately not in ai/pi/extensions/, which ai/pi/steps.sh installs into
 * ~/.pi/agent/extensions and Pi loads in every session. This one gates on
 * REVIEW_WORKTREE_DIR, which only the review pipeline sets, so installed
 * globally it would be inert in every interactive session while still writing a
 * log line for every tool call.
 *
 * REVIEW_ALLOWED_DIRS carries the invocation's add_dirs, delimiter-separated.
 * The review document lives under ~/.local/state/workbench/reviews/, outside
 * the worktree by design — gating on the worktree alone would refuse the one
 * write every phase exists to make.
 *
 * With the variable unset it blocks nothing. That is the fail-open direction on
 * purpose: this is a guard rail for an agent already restricted by its prompt
 * and tool list, not the boundary the pipeline relies on. A typo in the
 * variable's name must not silently turn a review into an unrestricted session,
 * so the absence is reported once rather than passing quietly.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { realpathSync } from "node:fs";
import { basename, delimiter, dirname, isAbsolute, relative, resolve, sep } from "node:path";

// Shell constructs that write. Matched against the whole command, so this is a
// coarse net: it catches the ordinary cases and accepts false positives on a
// command that merely mentions one. A review agent has no reason to run any of
// them, so refusing too much costs nothing here.
const WRITE_COMMAND_PATTERNS = [
  /\bcp\b/,
  /\bmv\b/,
  /\brm\b/,
  /\btee\b/,
  /\bdd\b/,
  /\btruncate\b/,
  /\binstall\b/,
  /\bsed\s+-i/,
  /\bperl\s+[^|]*-i/,
  /\b(?:curl|wget)\b[^|]*\s-[a-zA-Z]*[oO]\b/,
  // `apply` and `am` write arbitrary file content straight out of a patch,
  // which is the shape a fix pass reaches for when it wants a diff on disk.
  /\bgit\s+(?:commit|push|checkout|switch|restore|reset|clean|stash|rebase|merge|apply|am|cherry-pick|revert)\b/,
  // A redirect that names a destination, which `2>&1` and `2>/dev/null` do not.
  // Matching a bare `>` instead caught every `cmd 2>&1` an agent writes while
  // reading, and a guard that fires on ordinary reads is one whose refusals
  // stop being read.
  />>?\s*(?!&\d)(?!\/dev\/(?:null|stdout|stderr)\b)\S/,
];

// resolve() normalises `.` and `..` but does not follow symlinks, and on macOS
// /tmp is a symlink to /private/tmp: a root and a path that name the same
// directory in different spellings compare as unrelated, and the write is
// refused. A path that does not exist yet — which every new file is — has no
// realpath, so the nearest existing ancestor is canonicalised instead and the
// remainder appended.
function canonical(path: string): string {
  let head = resolve(path);
  const tail: string[] = [];
  for (;;) {
    try {
      return resolve(realpathSync(head), ...tail);
    } catch {
      const parent = dirname(head);
      // dirname("/") is "/": the root itself does not resolve, so give up and
      // fall back to the lexical form rather than looping forever.
      if (parent === head) return resolve(path);
      tail.unshift(basename(head));
      head = parent;
    }
  }
}

/** True when `path` is inside `root` — the same directory, or below it. */
function within(root: string, path: string): boolean {
  const resolvedRoot = resolve(root);
  const rel = relative(canonical(resolvedRoot), canonical(resolve(resolvedRoot, path)));
  // A bare startsWith("..") also rejects a sibling-named child such as
  // `...hidden`, which is inside the root.
  if (rel === "") return true;
  return rel !== ".." && !rel.startsWith(".." + sep) && !isAbsolute(rel);
}

export default function (pi: ExtensionAPI) {
  const worktreeDir = process.env.REVIEW_WORKTREE_DIR;
  const allowedDirs = [
    ...(worktreeDir ? [worktreeDir] : []),
    ...(process.env.REVIEW_ALLOWED_DIRS ?? "").split(delimiter).filter(Boolean),
  ];
  let warned = false;

  pi.on("tool_call", async (event) => {
    if (!worktreeDir) {
      if (!warned) {
        warned = true;
        console.error(
          JSON.stringify({
            ts: new Date().toISOString(),
            guard: "inactive",
            reason: "REVIEW_WORKTREE_DIR is unset; writes are not gated",
          }),
        );
      }
      return;
    }

    let summary = "";
    let blocked: string | null = null;

    if (isToolCallEventType("write", event) || isToolCallEventType("edit", event)) {
      summary = event.input.path;
      if (!allowedDirs.some((dir) => within(dir, event.input.path))) {
        // Every allowed root, not just the worktree: a refusal that names one
        // of two permitted directories reads as a bug in the guard.
        blocked = `${event.input.path} is outside the review's writable directories: ${allowedDirs.join(", ")}`;
      }
    } else if (isToolCallEventType("bash", event)) {
      summary = event.input.command.slice(0, 120);
      if (WRITE_COMMAND_PATTERNS.some((p) => p.test(event.input.command))) {
        blocked = `write-capable command in a review session: ${summary}`;
      }
    } else if (isToolCallEventType("read", event)) {
      summary = event.input.path;
    }

    console.error(
      JSON.stringify({
        ts: new Date().toISOString(),
        tool: event.toolName,
        summary,
        ...(blocked ? { blocked } : {}),
      }),
    );

    // terminate is left off: one refused write is a step the agent can route
    // around, not a reason to abandon a review that has already cost tokens.
    if (blocked) return { block: true, reason: blocked };
  });
}
