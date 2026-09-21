/**
 * Splitting a bash command into the statements it will actually run.
 *
 * Shared by the guards rather than copied into each: two guards that disagree
 * about what a statement is are worse than one, because which answer you get
 * depends on which guard saw the command first. The same reasoning the
 * individual detect.ts headers give for matching ai/claude/bin/claude-bash-guard
 * decision for decision, applied between the Pi guards themselves.
 *
 * Imports nothing, so tests/pi_extensions.bats can load it under plain `node`
 * the way it loads each detect.ts.
 *
 * Not an extension itself: step_pi_extensions skips a `_`-prefixed directory by
 * name, so this is never installed and never warned about. Node resolves
 * `../_shared/` from an extension's real path rather than its installed
 * symlink, so the import reaches this file in the checkout even though only the
 * extension directory is symlinked into ~/.pi/agent/extensions.
 */

/** Opens a heredoc, capturing the `-` that allows an indented terminator and the marker. */
const HEREDOC_OPEN = /<<(-?)\s*['"]?([A-Za-z_][A-Za-z0-9_]*)/;

/**
 * Every statement in `command`, with heredoc bodies dropped.
 *
 * A heredoc body is content being written to a file, not commands, so a
 * `sleep 300` or a `gh issue create` inside one is not an invocation — writing
 * a script for someone else to run is not the agent doing the thing. Claude's
 * guard skips those lines too, and this is the same rule.
 *
 * Only `<<-` lets the terminator be indented. Accepting indentation for a plain
 * `<<` would end the body early on a body line that happens to be the marker
 * word, and scan the rest of it as commands.
 *
 * Statements are split on the separators as well as on newlines, because
 * `sleep 2 && sleep 300` is two statements on one line — and a command whose
 * first line is a bare `cd` is the sanctioned form for a compound cd, so a
 * guard that only read the first statement would miss the one that matters.
 */
export function statements(command: string): string[] {
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
