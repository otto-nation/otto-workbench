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
 * Split one line on the control operators that end a statement — `;`, `&&`,
 * `||`, a standalone backgrounding `&`, and `|` — without breaking on an `&`
 * that belongs to a redirect (`2>&1`, `>&2`) instead.
 *
 * A naive `/[;&|]/` split cuts a redirect's `&` apart from the `>` in front
 * of it, so a trailing `2>&1` shatters the statement it sits inside into
 * fragments no longer than a couple of characters. Scanning char-by-char and
 * treating `&` right after `>` as part of the redirect keeps that statement
 * whole while still splitting on every other occurrence of the separators.
 */
function splitOnControlOperators(line: string): string[] {
  const parts: string[] = [];
  let current = "";

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if ((ch === "&" && line[i + 1] === "&") || (ch === "|" && line[i + 1] === "|")) {
      parts.push(current);
      current = "";
      i++;
      continue;
    }
    if (ch === ";" || ch === "|") {
      parts.push(current);
      current = "";
      continue;
    }
    if (ch === "&") {
      if (current.endsWith(">")) {
        current += ch;
        continue;
      }
      parts.push(current);
      current = "";
      continue;
    }
    current += ch;
  }
  parts.push(current);
  return parts;
}

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
    found.push(...splitOnControlOperators(line));

    const open = HEREDOC_OPEN.exec(line);
    if (open) {
      const indentable = open[1] ? "\\s*" : "";
      terminator = new RegExp(`^${indentable}${open[2]}\\s*$`);
    }
  }
  return found;
}
