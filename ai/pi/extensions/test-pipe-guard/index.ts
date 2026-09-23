/**
 * Refuses a bash command that pipes a test suite's status into a filter.
 *
 * `pytest tests/ | tail -6; echo "exit=$?"` reports `tail`'s status, not the
 * suite's — `false | tail -1` exits 0 — so a failing run reads as a pass. The
 * rule is in testing.md § Reading a Suite Result and was violated a dozen
 * times in one session by an agent that could recite it, which is what this is
 * for. Claude Code gets the same rule from ai/claude/bin/claude-bash-guard,
 * which does not run under Pi.
 *
 * Installed globally by ai/pi/steps.sh, alongside sleep-guard and
 * background-guard: it needs no environment set up, has nothing to say about the
 * repository it is in, and the behaviour it prevents is not specific to any
 * session.
 *
 * The predicate lives in ./detect.ts so it can be tested without the SDK — see
 * that file's header.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { pipedRunner } from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    if (!isToolCallEventType("bash", event)) return;

    const runner = pipedRunner(event.input.command);
    if (runner === null) return;

    // terminate is left off deliberately, matching sleep-guard: the agent should
    // carry on having rewritten the command, not lose the turn to the refusal.
    return {
      block: true,
      reason:
        `Piping \`${runner}\` into a filter reports the filter's exit status, not the ` +
        `suite's — \`false | tail -1\` exits 0, so a failing run reads as a pass and a ` +
        `\`$?\` read after the pipe is the filter's. Redirect to a file and read the ` +
        // /tmp specifically, not any path: review-guard refuses a redirect that
        // writes outside the scratch roots, and a remedy it then refuses leaves
        // the agent with no command it can run.
        `status, then grep the file: \`${runner} ... > /tmp/out.txt 2>&1\`, then read ` +
        `/tmp/out.txt. Do not append \`; echo $?\` to that — a trailing report becomes ` +
        `the command's own exit status, which is the same masking one statement later, ` +
        `and under job_start it is announced as success. exit-status-guard refuses it. ` +
        `\`set -o pipefail\` is accepted where a pipe is genuinely wanted. ` +
        `See testing.md § Reading a Suite Result.`,
    };
  });
}
