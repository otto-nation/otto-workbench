/**
 * The gh_ci_failures predicate, kept apart from the extension that uses it.
 *
 * index.ts imports the Pi SDK as a value, so it can only be loaded from
 * somewhere the SDK resolves — inside a Pi session. This file pulls in
 * nothing, so tests/pi_extensions_ci_failures.bats can run it under plain
 * `node`.
 *
 * Pipeline agents already omit the tool from --tools (backend_pi.PI_GITHUB_TOOLS).
 * Interactive sessions still get it from the github-pr package, and its
 * description tells the model to prefer it over `pr ci`. Prose in the
 * ci-failures skill did not hold; this does.
 */

export const TOOL_NAME = "gh_ci_failures";

export const BLOCK_REASON =
  "gh_ci_failures dumps full job logs into the prompt and has compacted sessions. " +
  "Use `pr ci` instead. Never paste job logs into the conversation.";

/**
 * True when NAME is the CI-log dump tool.
 *
 * Own-name equality, so `toString` is not a tool.
 */
export function isCiFailuresTool(name: string): boolean {
  return name === TOOL_NAME;
}

/** Active-tool list with gh_ci_failures removed. */
export function withoutCiFailures(names: readonly string[]): string[] {
  return names.filter((name) => !isCiFailuresTool(name));
}
