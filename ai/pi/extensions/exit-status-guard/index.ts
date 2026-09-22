/**
 * Refuses a command whose trailing statement discards the exit status it reports.
 *
 * `npm test > out.txt 2>&1; echo "EXIT=$?"` exits with echo's status — 0,
 * always. As a foreground bash call that is cosmetic; handed to `job_start` it
 * is not, because the completion notice the user sees is the process's exit
 * code. A failing suite is announced as "succeeded", and the real status sits
 * in a line of output nobody re-reads. That happened three times in one session
 * and is what this is for.
 *
 * The remedy is to let the runner be the last thing the command does, and read
 * the output afterwards — `job_output` returns it, so the redirect is usually
 * unnecessary too. Where the status genuinely has to be captured mid-command,
 * `set -o pipefail` plus an honest non-zero exit (`exit "$status"`) keeps the
 * process's own code truthful.
 *
 * This is test-pipe-guard's rule at the other end of the command: there a
 * filter downstream of the suite eats the status, here a trailing report does.
 * TEST_RUNNERS is imported from that guard rather than duplicated, so the two
 * cannot disagree about what a test run is.
 *
 * Installed globally by ai/pi/steps.sh alongside the other guards: it needs no
 * environment set up, has nothing to say about the repository it is in, and the
 * behaviour it prevents is not specific to any session.
 *
 * The predicate lives in ./detect.ts so it can be tested without the SDK — see
 * that file's header.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { TEST_RUNNERS } from "../test-pipe-guard/detect.ts";
import { maskedRunner, readsPrintedStatus } from "./detect.ts";

/** The `job_start` input, which the SDK does not type for us. */
type JobStartInput = { readonly command: string };

function refusal(runner: string, viaJob: boolean): string {
  const where = viaJob
    ? `The job's exit status becomes echo's — always 0 — so a failing run is ` +
      `reported to you and to the user as "succeeded", with the real status ` +
      `buried in output nobody re-reads. `
    : `The command's exit status becomes echo's — always 0 — so any caller ` +
      `reading it sees a pass. `;
  return (
    `Ending a command that runs \`${runner}\` with \`echo …$?\` discards the status ` +
    `it claims to report. ${where}` +
    `Let the runner be the last thing the command does and read its output ` +
    `afterwards${viaJob ? " with job_output" : ""} — the completion notice then ` +
    `carries the true status. If the status must be captured mid-command, end with ` +
    `an honest \`exit "$status"\` so the process's own code stays truthful. ` +
    `See testing.md § Reading a Suite Result.`
  );
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    // A foreground bash call: the agent reads the printed status, so this is
    // cosmetic rather than false green, and a redirect to a file is exempt.
    if (isToolCallEventType("bash", event)) {
      const runner = maskedRunner(
        event.input.command,
        TEST_RUNNERS,
        readsPrintedStatus("bash"),
      );
      if (runner === null) return;
      return { block: true, reason: refusal(runner, false) };
    }

    // The case this guard exists for. job_start surfaces the process's exit
    // code as a pass/fail notice, so nothing about the command redeems a
    // masked status — not even persisting it to a file.
    if (isToolCallEventType<"job_start", JobStartInput>("job_start", event)) {
      const runner = maskedRunner(
        event.input.command,
        TEST_RUNNERS,
        readsPrintedStatus("job_start"),
      );
      if (runner === null) return;
      return { block: true, reason: refusal(runner, true) };
    }
  });
}
