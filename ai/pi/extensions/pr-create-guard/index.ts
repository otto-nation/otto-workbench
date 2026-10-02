/**
 * Refuses `gh pr create`, which opens a PR that skips everything
 * `pr create` does.
 *
 * `pr create` resolves the repo's PR template and checks the body
 * against its section headers, appends a closing ref for each `--closes`,
 * assigns the PR to whoever opened it, and pushes the branch first. A bare
 * `gh pr create` does none of that, so the PR lands with no template, no issue
 * link, and no assignee — and the template check is the one that cannot be
 * repaired afterwards without a reviewer having already read the wrong thing.
 *
 * Claude Code gets the same rule from ai/claude/bin/claude-bash-guard; that
 * hook does not run under Pi. The two enforce one rule written down in
 * git-operations.md § PR Creation.
 *
 * The predicate lives in ./detect.ts so it can be tested without the SDK — see
 * that file's header, which also records why this rule is shared where most of
 * the Claude guard's are not. What stays here is only the wiring.
 */

import {
  isToolCallEventType,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { isPrCreate } from "./detect.ts";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    if (!isToolCallEventType("bash", event)) return;
    if (!isPrCreate(event.input.command)) return;

    // terminate is left off deliberately, as in the other guards: the agent
    // should carry on with the turn using the right command.
    return {
      block: true,
      reason:
        "Use pr create instead of gh pr create — it loads the repo's PR " +
        "template and checks the body against its sections, appends a closing ref for " +
        "each --closes, assigns the PR, and pushes the branch first. A bare gh pr create " +
        "opens a PR with none of those. Pass --draft so it goes through review, and " +
        "--repo-dir <path> when the working directory is not the target repo. " +
        "See git-operations.md § PR Creation.",
    };
  });
}
