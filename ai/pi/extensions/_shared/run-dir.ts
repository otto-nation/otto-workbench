/**
 * Whether a pi session is a `pr batch` run's own session or a PR target
 * session, and which directory it owns.
 *
 * `pr batch session RUN_ID` starts pi with `--session-id batch-<run-id>` and
 * WORKBENCH_RUN_DIR set to the run directory. ai/lib/batch/run_session.py
 * `recipe` owns those spellings.
 *
 * `pr target session` starts pi with `--session-id target-<hex>` ,
 * WORKBENCH_TARGET_DIR set to the target directory, and WORKBENCH_SESSION_ID
 * the expected id. ai/lib/batch/target_session.py owns those spellings.
 * tests/pi_run_session_contract_test.py and
 * tests/pi_target_session_contract_test.py fail when these copies differ.
 *
 * The directory variable alone does not make an orchestrator session: every
 * process that session starts inherits it, including the pi agents a
 * `pr … --fix` dispatches (ai/lib/agent/backend.py agent_env inherits
 * os.environ), and those must neither become orchestrators nor drop their
 * worktree claim. The session id is what only the orchestrator session
 * carries.
 *
 * Imports nothing, so node loads it in tests.
 */

export type Kind = "batch" | "target";

export interface KindSpec {
  dirEnv: string;
  idEnv: string | null;
  idPrefix: string;
  /** The `pr` command group that owns this kind's report and note. */
  group: string;
  noun: string;
  subject(id: string): string;
  intro(id: string, dir: string, cwd: string): string;
  rules(id: string): string[];
  reportArgv(id: string): string[];
  noteArgv(id: string): string[];
}

/** The kind of a stored session entry written before entries carried one. */
export const LEGACY_KIND: Kind = "batch";

export const KINDS: Record<Kind, KindSpec> = {
  batch: {
    dirEnv: "WORKBENCH_RUN_DIR",
    idEnv: null,
    idPrefix: "batch-",
    group: "batch",
    noun: "pr batch run",
    subject(id) {
      return "run " + id;
    },
    intro(id, dir, cwd) {
      return `You are the orchestrator of pr batch run ${id}. Its checkout is ${cwd}; its run directory is ${dir}.`;
    },
    rules(id) {
      return [
        `Now is read offline; \`pr batch report ${id} --format json\` without --offline asks GitHub too.`,
      ];
    },
    reportArgv(id) {
      return ["batch", "report", id, "--offline", "--format", "json"];
    },
    noteArgv(id) {
      return ["batch", "note", id];
    },
  },
  target: {
    dirEnv: "WORKBENCH_TARGET_DIR",
    idEnv: "WORKBENCH_SESSION_ID",
    idPrefix: "target-",
    group: "target",
    noun: "PR target",
    subject(id) {
      return "target " + id;
    },
    intro(id, dir, cwd) {
      return `You are the orchestrator of the PR target ${id}. Its checkout is ${cwd}; its history directory is ${dir}.`;
    },
    rules() {
      return [
        "This session runs pr commands and never edits files directly: a fix pass edits this checkout, and your edits would be committed over.",
      ];
    },
    reportArgv() {
      return ["target", "report"];
    },
    noteArgv() {
      return ["target", "note"];
    },
  },
};

/** The run id a run directory names: its last path component (batch.store.run_dir). */
export function runIdOf(runDir: string): string {
  const parts = runDir.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] ?? "";
}

/** The kind and directory when sessionId is that session's own id, else null. */
export function sessionOf(
  env: Record<string, string | undefined>,
  sessionId: string,
): { kind: Kind; dir: string } | null {
  for (const kind of Object.keys(KINDS) as Kind[]) {
    const spec = KINDS[kind];
    const dir = env[spec.dirEnv]?.trim();
    if (!dir) continue;
    const expected = spec.idEnv
      ? env[spec.idEnv]?.trim()
      : spec.idPrefix + runIdOf(dir);
    if (
      expected &&
      sessionId === expected &&
      sessionId.startsWith(spec.idPrefix)
    ) {
      return { kind, dir };
    }
  }
  return null;
}

/**
 * Whether a session started with `env` and `sessionId` claims its worktree.
 *
 * A batch run's own session and a PR target's own session do not
 * (../_shared/run-dir.ts says which those are). Batch: cwd is the run's
 * checkout, and batch reuses whichever worktree already holds an item's
 * branch (ai/lib/batch/steps.py ensure_worktree), so a claim there would make
 * that item's later `--fix` steps refuse. Target: cwd is the branch's own
 * worktree, where `pr … --fix` passes edit, so a claim would refuse them.
 * No Claude Code twin: only pi is ever started as an orchestrator session.
 */
export function claimsWorktree(
  env: Record<string, string | undefined>,
  sessionId: string,
): boolean {
  return sessionOf(env, sessionId) === null;
}
