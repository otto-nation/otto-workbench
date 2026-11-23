/**
 * Refuses `gh_ci_failures` and drops it from the active set.
 *
 * The tool dumps full job logs into the prompt and has compacted sessions.
 * Pipeline agents never receive it (`PI_GITHUB_TOOLS` in backend_pi.py);
 * interactive sessions still get it from the github-pr package, whose
 * description tells the model to prefer it over `pr ci`. Prose in the
 * ci-failures skill did not hold.
 *
 * Pi-only: Claude Code has no such tool. Installed globally by ai/pi/steps.sh
 * — the behaviour it prevents is not specific to any checkout.
 *
 * The predicate lives in ./detect.ts so it can be tested without the SDK.
 * What stays here is only the wiring.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  BLOCK_REASON,
  isCiFailuresTool,
  withoutCiFailures,
} from "./detect.ts";

function dropCiFailures(pi: ExtensionAPI) {
  const active = pi.getActiveTools();
  const next = withoutCiFailures(active);
  // session_start can run before every extension has registered its tools.
  // Rewriting the set then would freeze the incomplete list as the session's
  // tools. Only write when the dump tool is actually present.
  if (next.length === active.length) return;
  pi.setActiveTools(next);
}

export default function (pi: ExtensionAPI) {
  pi.on("session_start", async () => {
    dropCiFailures(pi);
  });

  pi.on("before_agent_start", async () => {
    dropCiFailures(pi);
  });

  pi.on("tool_call", async (event) => {
    if (!isCiFailuresTool(event.toolName)) return;

    // terminate is left off, as in the other guards: the agent carries on
    // with `pr ci` instead of dumping logs into the prompt.
    return { block: true, reason: BLOCK_REASON };
  });
}
