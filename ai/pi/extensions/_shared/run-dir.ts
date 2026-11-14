/**
 * Whether a pi session is a `pr batch` run's own session, and which run.
 *
 * `pr batch session RUN_ID` starts pi with `--session-id batch-<run-id>` and
 * WORKBENCH_RUN_DIR set to the run directory. ai/lib/batch/run_session.py
 * `recipe` owns both spellings; tests/pi_run_session_contract_test.py fails
 * when these copies differ.
 *
 * The variable alone does not make a run session: every process that session
 * starts inherits it, including the pi agents a `pr … --fix` dispatches
 * (ai/lib/agent/backend.py agent_env inherits os.environ), and those must
 * neither become orchestrators nor drop their worktree claim. The session id
 * is what only the run session carries.
 *
 * Imports nothing, so node loads it in tests.
 */

export const RUN_DIR_ENV = "WORKBENCH_RUN_DIR";
export const SESSION_ID_PREFIX = "batch-";

/** The run id a run directory names: its last path component (batch.store.run_dir). */
export function runIdOf(runDir: string): string {
  const parts = runDir.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] ?? "";
}

/** The run directory *env* names, or null when the variable is unset or blank. */
export function runDirFrom(env: Record<string, string | undefined>): string | null {
  const value = env[RUN_DIR_ENV]?.trim();
  return value ? value : null;
}

/** The run directory when *sessionId* is that run's own session (`batch-<run-id>`), else null. */
export function runSessionDir(
  env: Record<string, string | undefined>,
  sessionId: string,
): string | null {
  const dir = runDirFrom(env);
  if (!dir) return null;
  return sessionId === SESSION_ID_PREFIX + runIdOf(dir) ? dir : null;
}
