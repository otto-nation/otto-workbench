/**
 * Splitting a bash command into the statements it will actually run.
 *
 * Shared by the guards rather than copied into each: two guards that disagree
 * about what a statement is are worse than one, because which answer you get
 * depends on which guard saw the command first. The same reasoning the
 * individual detect.ts headers give for matching ai/claude/bin/claude-bash-guard
 * decision for decision, applied between the Pi guards themselves.
 *
 * Splitting is done over the token scan in ./tokenize.ts rather than over the
 * raw line. A separator inside quotes is an argument, not a boundary, and
 * reading it as one was a complete bypass of every rule downstream:
 * `bash -c 'rm -rf x; echo done'` was cut into two fragments, neither of which
 * parsed as a shell wrapper or as a write, and the delete was permitted. One
 * `; true` appended to any refused command defeated the guard.
 *
 * Imports only ./tokenize.ts, which imports nothing, so tests/pi_extensions.bats
 * can load it under plain `node` the way it loads each detect.ts.
 *
 * Not an extension itself: step_pi_extensions skips a `_`-prefixed directory by
 * name, so this is never installed and never warned about. Node resolves
 * `../_shared/` from an extension's real path rather than its installed
 * symlink, so the import reaches this file in the checkout even though only the
 * extension directory is symlinked into ~/.pi/agent/extensions.
 */

import { span, tokenize, type Token } from "./tokenize.ts";

/** Opens a heredoc, capturing the `-` that allows an indented terminator and the marker. */
const HEREDOC_OPEN = /<<(-?)\s*['"]?([A-Za-z_][A-Za-z0-9_]*)/;

/** The operators that end a statement, as the tokenizer spells them. */
const STATEMENT_SEPARATORS = new Set([";", "&&", "||", "|", "&", ";;"]);

/**
 * Split one line on the control operators that end a statement — `;`, `&&`,
 * `||`, a standalone backgrounding `&`, and `|` — without breaking on an `&`
 * that belongs to a redirect (`2>&1`, `>&2`, `&>file`, `&>>file`) instead.
 *
 * A naive `/[;&|]/` split cuts a redirect's `&` apart from the `>` next to
 * it, so a trailing `2>&1` shatters the statement it sits inside into
 * fragments no longer than a couple of characters, and a leading `&>file`
 * would do the same in the other direction. Scanning char-by-char and
 * treating `&` right after or right before `>` as part of the redirect keeps
 * that statement whole while still splitting on every other occurrence of
 * the separators.
 */
function splitOnControlOperators(line: string): string[] {
  const parts: string[] = [];
  let current: Token[] = [];

  // Sliced from the line rather than rejoined from `raw`: joining with spaces
  // reshapes the text every downstream rule is written against, turning
  // `2>&1` into `2 > & 1`.
  const flush = () => {
    parts.push(span(line, current));
    current = [];
  };

  const tokens = tokenize(line);
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];

    // Quoted tokens are never separators — the whole reason this reads the
    // scan instead of the characters.
    if (tok.operator && STATEMENT_SEPARATORS.has(tok.value)) {
      // An `&` belonging to a redirect is not a separator: `2>&1` scans as
      // `2`, `>`, `&`, `1`, and cutting at that `&` shatters the statement it
      // sits inside. The redirect before it is what tells the two apart.
      const previous = tokens[i - 1];
      if (tok.value === "&" && previous?.operator && previous.value.includes(">")) {
        current.push(tok);
        continue;
      }
      flush();
      continue;
    }
    current.push(tok);
  }
  flush();
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
