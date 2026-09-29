import { execFileSync } from "node:child_process";

/**
 * Records this Pi session as editing a worktree, kept apart from the wiring.
 *
 * index.ts imports from the Pi SDK as a value, so it can only load inside a
 * session. This file pulls in one node builtin and nothing else, so
 * tests/pi_extensions.bats can exercise it under plain `node` — which matters
 * most here, because a renamed binary or a moved lock path makes the claim
 * silently never happen, and a session that never recorded itself looks
 * exactly like one nobody is editing.
 *
 * The record is ai/lib/core/session_lock.py. Claude Code writes the same fact
 * through ai/claude/bin/claude-session-lock, and ai/lib/fix/engine.py reads
 * it. One record, two writers, so a fix pass does not have to know which
 * harness is holding the tree.
 *
 * Why a record rather than a lock this process holds: Pi *could* hold a flock
 * for the session's lifetime, but Claude Code cannot — its session hooks are
 * short-lived subprocesses. Two harnesses answering the same question two
 * ways is a protocol a reader has to reconcile, so both name a pid instead
 * and let liveness be the verdict.
 */

/** Where `with-session-lock` lives, resolved from this file, not $PATH. */
const WITH_SESSION_LOCK = new URL(
  "../../../../bin/local/with-session-lock",
  import.meta.url,
).pathname;

/**
 * Record `pid` as editing `cwd`. Returns whether the claim was made.
 *
 * Best-effort by design: a session that cannot record itself is unprotected,
 * which is the behaviour before this existed. Throwing here would take down
 * the session start over a lock.
 */
export function acquireFor(
  cwd: string,
  pid: number,
  sessionId: string,
): boolean {
  try {
    execFileSync(
      WITH_SESSION_LOCK,
      [
        cwd,
        "--acquire",
        "--pid",
        String(pid),
        "--harness",
        "pi",
        "--session-id",
        sessionId,
        "--command",
        "pi",
      ],
      { stdio: "ignore" },
    );
    return true;
  } catch {
    return false;
  }
}

/** Drop this session's claim on `cwd`. */
export function releaseFor(cwd: string, pid: number): boolean {
  try {
    execFileSync(WITH_SESSION_LOCK, [cwd, "--release", "--pid", String(pid)], {
      stdio: "ignore",
    });
    return true;
  } catch {
    return false;
  }
}
