/**
 * The issue-filing predicate, kept apart from the extension that uses it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file imports nothing, which is what lets tests/pi_extensions.bats run it under
 * plain `node` and assert the shapes it does and does not match.
 *
 * What counts as filing here is meant to match the `gh issue create` rule in
 * ai/claude/bin/claude-bash-guard decision for decision. Two guards enforcing
 * one rule that disagree about a given command are worse than one guard,
 * because which answer you get depends on which harness you happen to be in.
 *
 * Only the command half lives here. Whether the branch is under a review with
 * open findings is a filesystem question, so index.ts asks it — the same split
 * the Claude guard makes between its pattern and `_branch_review_has_open_findings`.
 */

/**
 * `gh issue create` as the command being run.
 *
 * Anchored to a statement head so `gh issue view`, `gh issue list`, and a grep
 * over a script mentioning the phrase are not matched. The subcommand words may
 * be separated by any run of whitespace, which is the only variation the form
 * takes in practice.
 *
 * Tested against one statement at a time (see `statements()` below), so the
 * head is a plain `^\s*` — the leading whitespace a separator like `&& ` or
 * `; ` leaves behind on the fragment after it, rather than the separator
 * itself.
 *
 * ceiling: `gh` reached through an alias or a wrapper script is not matched.
 * Upgrade if one shows up; `gh issue create` is the form an agent writes, and
 * the Claude guard reads it the same way.
 */
const ISSUE_CREATE = /^\s*gh\s+issue\s+create(\s|$)/;

/** Opens a heredoc, capturing the `-` that allows an indented terminator and the marker. */
const HEREDOC_OPEN = /<<(-?)\s*['"]?([A-Za-z_][A-Za-z0-9_]*)/;

/**
 * Every statement in `command`, with heredoc bodies dropped.
 *
 * Bare `^`/`$` in ISSUE_CREATE do not cross a literal newline, so a `cd`-then-
 * filing command spanning two lines — the sanctioned form for a compound `cd`,
 * per bash-tool.md § Avoid Compound `cd` Commands — would otherwise slip past
 * a single whole-string match. Splitting on newlines as well as `[;&|]`, the
 * way sleep-guard/detect.ts already does, is what lets the anchors see each
 * statement on its own.
 *
 * A heredoc body is content being written to a file, not commands, so a
 * `gh issue create` inside one is not an invocation — writing a script for
 * someone else to run is not the agent filing.
 */
function statements(command: string): string[] {
  const found: string[] = [];
  let terminator: RegExp | null = null;

  for (const line of command.split("\n")) {
    if (terminator) {
      if (terminator.test(line)) terminator = null;
      continue;
    }
    found.push(...line.split(/[;&|]/));

    const open = HEREDOC_OPEN.exec(line);
    if (open) {
      const indentable = open[1] ? "\\s*" : "";
      terminator = new RegExp(`^${indentable}${open[2]}\\s*$`);
    }
  }
  return found;
}

/** True when COMMAND files a new issue. */
export function isIssueFiling(command: string): boolean {
  return statements(command).some((statement) => ISSUE_CREATE.test(statement));
}
