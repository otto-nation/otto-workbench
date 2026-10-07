import { execFileSync } from "node:child_process";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Whether a tree is being validated right now, kept apart from the extension.
 *
 * index.ts imports its event-type helper from the Pi SDK as a value, so it can
 * only be loaded from inside a Pi session. This file pulls in two node
 * builtins and nothing else, so tests/pi_extensions_issues.bats can run it under
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
const WITH_TREE_LOCK = fileURLToPath(
  new URL("../../../../bin/local/with-tree-lock", import.meta.url),
);

export const REFUSAL =
  "A validator holds this tree — a suite or a gate is reading the files you are " +
  "about to change. Editing now invalidates that run: it reports against a tree " +
  "that no longer exists, and a green result would be exactly as green. Wait for " +
  "it to finish, or make the edit in another worktree.";

/**
 * The line `with-tree-lock --check` prints for a free tree, after the tree path.
 * Owned by FREE_SUFFIX in ai/lib/core/tree_lock_cli.py; a test in
 * tests/pi_extensions_issues.bats asserts the two are equal.
 */
export const FREE_SUFFIX = ": not being validated";

/** The line it prints for a held tree. Owned by HELD_SUFFIX in tree_lock_cli.py, tested the same way. */
export const HELD_SUFFIX = ": validating";

/**
 * How long either child may run before the probe gives up and says "unknown".
 * Matches core.timeouts.LOCAL, which bounds the same git call on the Python side.
 */
export const PROBE_TIMEOUT_MS = 10_000;

/**
 * What `git rev-parse` says, under LC_ALL=C, when there is no repository to ask
 * about: outside one, or a -C directory that does not exist. Exit 128 alone is
 * not that — git uses it for every fatal error, dubious ownership and a corrupt
 * repository included — so the message is what separates "nothing to guard"
 * from "git could not answer". Mirrors _NOT_A_REPO_MARKERS in tree_lock.py.
 */
const NOT_A_REPO_MARKERS = ["not a git repository", "cannot change to"];

/**
 * A probe's verdict. "unknown" carries the reason the probe could not answer.
 *
 * Kept apart from "free" because both make the guard stay silent, and a guard
 * that stayed silent because its probe broke is otherwise indistinguishable
 * from one with nothing to say — under load that is a test reporting a held
 * lock as free with no clue why.
 */
export type ProbeState = "held" | "free" | "unknown";

export interface Probe {
  state: ProbeState;
  reason: string;
}

/** The answer for one edit: the refusal (or null), and why the probe failed if it did. */
export interface LockVerdict {
  refusal: string | null;
  unknownReason: string;
}

/** A child process failure, described: exit status, signal, or spawn error, then its stderr. */
function describeFailure(what: string, err: unknown): string {
  const e = err as { status?: number | null; signal?: string | null; code?: string; stderr?: unknown };
  const how =
    e.code === "ETIMEDOUT"
      ? "timed out"
      : typeof e.status === "number"
      ? `exited ${e.status}`
      : e.signal
        ? `was killed by ${e.signal}`
        : `could not run (${e.code ?? String(err)})`;
  const stderr = String(e.stderr ?? "").trim();
  return stderr ? `${what} ${how}: ${stderr}` : `${what} ${how}`;
}

/**
 * The repository working tree containing PATH: root "" with no reason when
 * there is none, root "" with a reason when git could not be asked.
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
export function gitRootFor(
  path: string,
  timeoutMs: number = PROBE_TIMEOUT_MS,
): { root: string; reason: string } {
  const env = { ...process.env };
  // git localises its messages, and the not-a-repo check below reads one.
  env.LC_ALL = "C";
  delete env.GIT_DIR;
  delete env.GIT_WORK_TREE;
  delete env.GIT_INDEX_FILE;
  delete env.GIT_COMMON_DIR;

  try {
    const root = execFileSync("git", ["-C", dirname(path), "rev-parse", "--show-toplevel"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      env,
      timeout: timeoutMs,
    }).trim();
    return { root, reason: "" };
  } catch (err) {
    const e = err as { status?: number | null; stderr?: unknown };
    const stderr = String(e.stderr ?? "");
    if (e.status === 128 && NOT_A_REPO_MARKERS.some((marker) => stderr.includes(marker))) {
      return { root: "", reason: "" };
    }
    return { root: "", reason: describeFailure("git rev-parse", err) };
  }
}

/**
 * Whether something holds the validation lock on TREE.
 *
 * Shells to `with-tree-lock --check`, which is `tree_lock.probe()`, rather
 * than reimplementing the probe: node has no `fcntl.flock`, and a second
 * implementation of the one fact both harnesses read is the way they come to
 * disagree. Each verdict needs its exit status *and* its line: held is exit 0
 * with the held line, free is exit 1 with the free line. A python3 or shim that
 * fails before the probe runs exits 1, and one that stubs it out exits 0, so
 * a bare status is not evidence either way. Anything else, exit 3 included, is
 * unknown.
 *
 * ceiling: one `git` spawn plus one `python3` spawn per Edit/Write call,
 * measured at well under the 50ms an Edit costs elsewhere. Upgrade to an
 * in-process flock probe if a measurement puts this over 50ms, or if Pi grows
 * a way to keep a helper process alive across tool calls.
 */
export function probeTree(tree: string, timeoutMs: number = PROBE_TIMEOUT_MS): Probe {
  try {
    const stdout = execFileSync(WITH_TREE_LOCK, ["--check", tree], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      timeout: timeoutMs,
    });
    if (stdout.includes(HELD_SUFFIX)) return { state: "held", reason: "" };
    return {
      state: "unknown",
      reason: `with-tree-lock --check exited 0 without the "${HELD_SUFFIX.slice(2)}" line`,
    };
  } catch (err) {
    const e = err as { status?: number | null; stdout?: unknown };
    if (e.status === 1 && String(e.stdout ?? "").includes(FREE_SUFFIX)) {
      return { state: "free", reason: "" };
    }
    return { state: "unknown", reason: describeFailure("with-tree-lock --check", err) };
  }
}

/**
 * The refusal for an edit to PATH (or null), and why the probe failed if it did.
 *
 * Fails open at every step — no git, no tree, no lock, or a probe that cannot
 * run all mean no refusal. A guard that refused because it could not answer
 * would block every edit on the machine the moment the binary moved. The
 * reason is carried so a caller that can say so — a test, a diagnostic — does.
 *
 * WORKBENCH_TREE_LOCK is deliberately not read. That variable is the writers'
 * reentrancy marker, set so a validator re-execing under the wrapper does not
 * deadlock against itself; it says nothing about whether the tree an agent is
 * editing is under validation, and honouring it here would let any process
 * that inherited it edit straight through the lock.
 */
export function lockVerdict(path: string, timeoutMs: number = PROBE_TIMEOUT_MS): LockVerdict {
  const { root, reason } = gitRootFor(path, timeoutMs);
  if (!root) return { refusal: null, unknownReason: reason };
  const probe = probeTree(root, timeoutMs);
  if (probe.state === "unknown") return { refusal: null, unknownReason: probe.reason };
  if (probe.state === "free") return { refusal: null, unknownReason: "" };

  // No pid: holders() can be empty while the lock is held — a record torn
  // mid-write, or a holder that never wrote one — so naming one would be a
  // detail the guard cannot stand behind.
  return { refusal: REFUSAL, unknownReason: "" };
}

/** The refusal for an edit to PATH, or null when the edit may proceed. */
export function lockRefusal(path: string): string | null {
  return lockVerdict(path).refusal;
}
