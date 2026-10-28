import {
  CLASSIFY_TIMEOUT_MS,
  OWNERS_CHECK as SHARED_OWNERS_CHECK,
  mightWriteGithub,
  runOwnersCheck,
} from "../_shared/github-writes.ts";

/**
 * The owner-boundary refusal, kept apart from the extension that uses it.
 *
 * index.ts imports its event-type helper from the Pi SDK as a value, so it can
 * only be loaded inside a Pi session. This file pulls in ../_shared only, so
 * tests/pi_extensions_upstream.bats runs it under `node` — including the half
 * that would otherwise break silently: a moved owners-check must read as a
 * check that could not run, and refuse, never as an allow.
 *
 * Pre-filter here, policy in Python: `mightWriteGithub` decides whether to ask,
 * and `owners-check --cwd` (ai/lib/gh/owners.py) decides the answer — whether
 * the command writes at all, which owners are ours, which repository it
 * targets. Exit 0 allows (a read always is), 2 refuses with the reason on
 * stderr — the reason carries its own advice, so the wrapper adds none —
 * anything else refuses as a failed check. Claude Code runs the same
 * check from ai/claude/bin/claude-bash-guard.
 */

/**
 * The user's override for the workbench's own publishing. A command naming it
 * is refused. Kept equal to ALLOW_UPSTREAM_ENV in ai/lib/gh/owners.py and
 * UPSTREAM_OVERRIDE_VAR in claude-bash-guard by tests/pi_extensions_upstream.bats.
 */
export const ALLOW_UPSTREAM_ENV = "WORKBENCH_ALLOW_UPSTREAM";

/** The owners-check this guard runs by default — the shared locator's answer. */
export const OWNERS_CHECK: string = SHARED_OWNERS_CHECK;

/** The exit owners-check refuses with. Owned by REFUSED in ai/lib/cli/owners_check.py. */
export const REFUSED_STATUS = 2;

/** The bound on one check — the shared owners-check timeout. */
export const CHECK_TIMEOUT_MS = CLASSIFY_TIMEOUT_MS;

const CITATION = "See general.md § Upstream Repositories.";

/** True when COMMAND spells the override variable anywhere. */
export function namesOverride(command: string): boolean {
  return command.includes(ALLOW_UPSTREAM_ENV);
}

/** How a check that gave no verdict ended, for the refusal text. */
function howItFailed(run: ReturnType<typeof runOwnersCheck>, said: string, timeoutMs: number): string {
  const code = (run.error as NodeJS.ErrnoException | undefined)?.code;
  if (code === "ETIMEDOUT") return `owners-check gave no answer within ${timeoutMs / 1000}s`;
  if (run.error) return `owners-check could not be started: ${code ?? String(run.error)}`;
  if (typeof run.status === "number") {
    return `owners-check failed, exited ${run.status}${said ? `: ${said}` : ""}`;
  }
  return `owners-check was killed by ${run.signal ?? "a signal"}`;
}

/** The refusal for COMMAND run from CWD, or null when it may run. */
export function upstreamRefusal(
  command: string,
  cwd: string,
  ownersCheck: string = OWNERS_CHECK,
  timeoutMs: number = CHECK_TIMEOUT_MS,
): string | null {
  if (namesOverride(command)) {
    return (
      `${ALLOW_UPSTREAM_ENV} is the user's override for publishing upstream, and an agent ` +
      `command may not name it. Draft the command and the text for the user to run themselves. ${CITATION}`
    );
  }
  if (!mightWriteGithub(command)) return null;
  const run = runOwnersCheck(["--cwd", cwd, "--", command], ownersCheck, timeoutMs);
  if (!run.error && run.status === 0) return null;
  const said = String(run.stderr ?? "").trim().replace(/\s*\n\s*/g, " ");
  if (!run.error && run.status === REFUSED_STATUS) {
    return (
      `This GitHub write is refused at the owner boundary: ${said}. ${CITATION}`
    );
  }
  return (
    `This command might be a GitHub write, and the owner-boundary check could not run ` +
    `(${howItFailed(run, said, timeoutMs)}), so it is refused. ${CITATION}`
  );
}
