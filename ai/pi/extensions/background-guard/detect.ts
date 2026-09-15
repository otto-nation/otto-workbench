/**
 * The detached-backgrounding predicate, kept apart from the extension using it.
 *
 * index.ts imports `isToolCallEventType` from the Pi SDK as a value, so it can
 * only be loaded from somewhere the SDK resolves — inside a Pi session. This
 * file imports nothing, which is what lets tests/pi_extensions.bats run it under
 * plain `node` and assert the shapes it does and does not match.
 *
 * What counts as backgrounding here is meant to match the `re_background` and
 * `re_nohup` rules in ai/claude/bin/claude-bash-guard decision for decision:
 * same operators, same heredoc exemption, same quote stripping. Two guards
 * enforcing one rule that disagree about a given command are worse than one
 * guard, because which answer you get depends on which harness you are in.
 */

/**
 * A standalone `&`, the background operator.
 *
 * `&` only backgrounds when it stands alone: `&&` is a conjunction, `2>&1` and
 * `&>` are redirects, `|&` is a pipe, and `;&`/`;;&` fall through to the next
 * branch of a case. Each is excluded by the character on one side of the match,
 * which is why both sides are tested rather than just the operator.
 */
const BACKGROUND = /(^|[^&>|;])&([^&>]|$)/;

/** `nohup` as the command being run, rather than the word appearing in an argument. */
const NOHUP = /(^|[;&|]\s*)nohup\s/;

/** Opens a heredoc, capturing the `-` that allows an indented terminator and the marker. */
const HEREDOC_OPEN = /<<(-?)\s*['"]?([A-Za-z_][A-Za-z0-9_]*)/;

/**
 * `command` with heredoc bodies dropped and quoted spans blanked.
 *
 * A heredoc body is content being written to a file, not commands, so an `&` in
 * one is not backgrounding — writing a script for someone else to run is not the
 * agent detaching a shell. Quoted spans go for the same reason: an `&` inside
 * `grep 'a & b'` is a literal, and a commit message mentioning nohup is prose.
 *
 * Only `<<-` lets the terminator be indented. Accepting indentation for a plain
 * `<<` would end the body early on a body line that happens to be the marker
 * word, and scan the rest of it as commands.
 */
function scannable(command: string): string {
  const lines: string[] = [];
  let terminator: RegExp | null = null;

  for (const line of command.split("\n")) {
    if (terminator) {
      if (terminator.test(line)) terminator = null;
      continue;
    }
    lines.push(line);

    const open = HEREDOC_OPEN.exec(line);
    if (open) {
      const indentable = open[1] ? "\\s*" : "";
      terminator = new RegExp(`^${indentable}${open[2]}\\s*$`);
    }
  }
  return lines.join("; ").replace(/'[^']*'/g, "").replace(/"[^"]*"/g, "");
}

/** Whether `command` detaches a shell the harness cannot see. */
export function isDetachedBackground(command: string): boolean {
  const text = scannable(command);
  return BACKGROUND.test(text) || NOHUP.test(text);
}
