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
 * The user's override for publishing upstream. A command naming it is refused.
 * Kept equal to ALLOW_UPSTREAM_ENV in ai/lib/gh/owners.py and the first entry of
 * UPSTREAM_OVERRIDE_VARS in claude-bash-guard by tests/pi_extensions_upstream.bats.
 */
export const ALLOW_UPSTREAM_ENV = "WORKBENCH_ALLOW_UPSTREAM";

/**
 * The user's override for publishing text that references another owner group.
 * A command naming it is refused. Kept equal to ALLOW_CROSS_REF_ENV in
 * ai/lib/gh/owners.py and the rest of UPSTREAM_OVERRIDE_VARS in claude-bash-guard
 * by tests/pi_extensions_upstream.bats.
 */
export const ALLOW_CROSS_REF_ENV = "WORKBENCH_ALLOW_CROSS_REF";

/** Both override names, in the same order as UPSTREAM_OVERRIDE_VARS in claude-bash-guard. */
export const OVERRIDE_ENV_VARS = [ALLOW_UPSTREAM_ENV, ALLOW_CROSS_REF_ENV] as const;

/** The owners-check this guard runs by default — the shared locator's answer. */
export const OWNERS_CHECK: string = SHARED_OWNERS_CHECK;

/** The exit owners-check refuses with. Owned by REFUSED in ai/lib/cli/owners_check.py. */
export const REFUSED_STATUS = 2;

/** The bound on one check — the shared owners-check timeout. */
export const CHECK_TIMEOUT_MS = CLASSIFY_TIMEOUT_MS;

const CITATION = "See general.md § Upstream Repositories.";
const CROSS_REF_CITATION = "See general.md § Cross-Group References.";

/** The override name COMMAND spells, or false when it names neither. */
export function namesOverride(command: string): string | false {
  for (const name of OVERRIDE_ENV_VARS) {
    if (command.includes(name)) return name;
  }
  return false;
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
  const named = namesOverride(command);
  if (named === ALLOW_CROSS_REF_ENV) {
    return (
      `${named} is the user's override for publishing text that references another owner group, and an agent ` +
      `command may not name it. Reword the text to drop the reference, or ask the user to publish it. ${CROSS_REF_CITATION}`
    );
  }
  if (named) {
    return (
      `${named} is the user's override for publishing upstream, and an agent ` +
      `command may not name it. Draft the command and the text for the user to run themselves. ${CITATION}`
    );
  }
  if (!mightWriteGithub(command)) return null;
  return checkRefusal(runOwnersCheck(["--cwd", cwd, "--", command], ownersCheck, timeoutMs), timeoutMs);
}

/**
 * The refusal an owners-check RUN amounts to, or null when it allowed.
 * WHAT names the write in the refusal text.
 *
 * Exit 0 allows; exit 2 refuses with the checker's own reason; anything else
 * is a check that could not run, which refuses. Shared by the bash path above
 * and the tool-call path in ./tool-calls.ts so both read one answer one way.
 */
export function checkRefusal(
  run: ReturnType<typeof runOwnersCheck>,
  timeoutMs: number,
  said: string = String(run.stderr ?? "").trim().replace(/\s*\n\s*/g, " "),
  what: string = "GitHub write",
): string | null {
  if (!run.error && run.status === 0) return null;
  if (!run.error && run.status === REFUSED_STATUS) {
    return (
      `This ${what} is refused at the owner boundary: ${said}. ${CITATION}`
    );
  }
  return (
    `This command might be a ${what}, and the owner-boundary check could not run ` +
    `(${howItFailed(run, said, timeoutMs)}), so it is refused. ${CITATION}`
  );
}
