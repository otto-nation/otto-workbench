/**
 * The alternative sleep-guard and background-guard name when they refuse.
 *
 * The block itself does not depend on which tools are active — `&`, `nohup`,
 * and a wait-sleep are refused either way. Only the reason changes. When
 * `job_start` is among the tools declared to the model, that is the
 * alternative. When it is not — a one-shot session, a restricted tool list —
 * naming it points at a tool that is not there, so the command has to run in
 * the foreground instead.
 *
 * Imports nothing, so tests/pi_extensions_tokenize.bats can load it under
 * plain `node` the way it loads each detect.ts.
 */

/** Exact name `getActiveTools()` reports when the job facility is declared. */
const JOB_START = "job_start";

const SEE = "See general.md § Waiting on Background Work.";

/**
 * What to do instead when no job tool is on offer.
 *
 * Shared by both guards rather than copied into each: two refusals that
 * disagree about the alternative are worse than one, because which answer
 * you get depends on which guard saw the command first.
 */
export const FOREGROUND_WAIT =
  "Run the command in the foreground with the bash tool's timeout and do not " +
  "end the turn while the work is outstanding.";

/** Whether `job_start` is among the tools declared to the model. */
export function jobStartIsActive(activeTools: readonly string[]): boolean {
  return activeTools.includes(JOB_START);
}

/**
 * The block reason for a detached shell or a wait-sleep.
 *
 * `kind` picks the lead; `jobStartActive` picks the alternative. Sleep needs
 * the duration so the reason can name the wait it refused.
 */
export function waitBlockReason(
  kind: "background" | "sleep",
  jobStartActive: boolean,
  sleepSeconds?: number,
): string {
  if (kind === "background") {
    const lead =
      "Backgrounding with & or nohup detaches a shell nothing reports on, so the only " +
      "way left to learn it finished is to poll it. ";
    if (jobStartActive) {
      return (
        lead +
        "Use job_start instead — it messages you on completion and takes " +
        "timeout_minutes to bound the run. Redirecting the " +
        "output to a file in the repo is part of the same pattern: job_start returns it. " +
        SEE
      );
    }
    return lead + FOREGROUND_WAIT + " " + SEE;
  }

  const lead =
    `A sleep of ${sleepSeconds}s is a wait for something that reports its own completion. `;
  if (jobStartActive) {
    return (
      lead +
      "A background job messages you when it finishes — do nothing and wait, or do other " +
      "work in the meantime. To bound how long a job may run, pass timeout_minutes to " +
      "job_start rather than sleeping beside it. " +
      SEE
    );
  }
  return lead + FOREGROUND_WAIT + " " + SEE;
}
