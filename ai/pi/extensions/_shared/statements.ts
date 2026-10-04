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
 * Imports only ./tokenize.ts, which imports nothing, so tests/pi_extensions_tokenize.bats
 * can load it under plain `node` the way it loads each detect.ts.
 *
 * Not an extension itself: step_pi_extensions skips a `_`-prefixed directory by
 * name, so this is never installed and never warned about. Node resolves
 * `../_shared/` from an extension's real path rather than its installed
 * symlink, so the import reaches this file in the checkout even though only the
 * extension directory is symlinked into ~/.pi/agent/extensions.
 */

import { hasUnparsed, span, tokenize, type Token } from "./tokenize.ts";

/** Opens a heredoc, capturing the `-` that allows an indented terminator and the marker. */
const HEREDOC_OPEN = /<<(-?)\s*['"]?([A-Za-z_][A-Za-z0-9_]*)/;

/**
 * The operators that end a statement, as the tokenizer spells them.
 *
 * `|&` is a pipe like `|`, not a backgrounding `&`: it pipes stdout *and*
 * stderr, and bash documents it as shorthand for `2>&1 |`. It was missing here
 * and from the Claude guard, so `pytest tests/ |& tail` — a suite whose status
 * a filter discards — was read as having no pipe at all.
 */
const STATEMENT_SEPARATORS = new Set([";", "&&", "||", "|", "|&", "&", ";;"]);

/**
 * One statement, and whether its output feeds the next one.
 *
 * `|` ends a statement like the other separators do, so a pipeline arrives as
 * several statements rather than one. A caller that needs the pipeline back —
 * the test-pipe rule, which asks what the *last* stage is — regroups on this
 * flag instead of re-splitting the text on `|`, because re-splitting would cut
 * a `|` inside quotes: `pytest tests/ | grep -q 'a|b'` would read as ending in
 * `b'` rather than in `grep`, and a suite piped into a filter would pass.
 *
 * A record rather than a pair, so a caller reads `stage.pipedIntoNext` instead
 * of learning which element of a tuple means what.
 */
export interface Statement {
  text: string;
  pipedIntoNext: boolean;
}

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
/**
 * The separators, for the fallback split below, capturing the run that split so
 * the caller can still tell a pipe from a `;`.
 *
 * Spelled as a character class rather than reusing STATEMENT_SEPARATORS because
 * this one cuts raw text the tokenizer could not read.
 */
const SEPARATOR_RUN = /([;&|]+)/;

function splitOnControlOperators(line: string): Statement[] {
  const parts: Statement[] = [];
  let current: Token[] = [];

  // Sliced from the line rather than rejoined from `raw`: joining with spaces
  // reshapes the text every downstream rule is written against, turning
  // `2>&1` into `2 > & 1`.
  const flush = (separator: string) => {
    parts.push({
      text: span(line, current),
      pipedIntoNext: separator === "|" || separator === "|&",
    });
    current = [];
  };

  const tokens = tokenize(line);

  // An unterminated quote means the scan could not find where the span ends, so
  // everything after it was taken as one word and any separator inside it was
  // never seen. Treating that as a single statement is how a guard silently
  // stops working: `echo it's; npm run dev &` really does background a shell,
  // and one apostrophe earlier in the line would hide it. Falling back to a
  // split on the separator characters over-splits a genuine quoted argument,
  // which costs at most one refusal of a command that was already unparseable.
  if (hasUnparsed(tokens)) {
    // `split` with one capture group yields text, separator, text, … so the odd
    // entries say what ended each statement. A run containing `|` is a pipe:
    // dropping that made `pytest tests/ | grep 'it's'` — a suite piped into a
    // filter, with an apostrophe in the pattern — read as two unrelated
    // statements and pass.
    const parts = line.split(SEPARATOR_RUN);
    const fallback: Statement[] = [];
    for (let i = 0; i < parts.length; i += 2) {
      fallback.push({
        text: parts[i],
        pipedIntoNext: (parts[i + 1] ?? "").includes("|"),
      });
    }
    return fallback;
  }

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
      flush(tok.value);
      continue;
    }
    current.push(tok);
  }
  // Nothing follows the last statement on a line, so it feeds nothing.
  flush("");
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
  return splitStatements(command).map((statement) => statement.text);
}

/**
 * Every statement in `command` with its pipe flag, for the callers that need to
 * see a pipeline as a pipeline.
 *
 * The same walk `statements()` reports; that one is the common view and this is
 * the full one, so the two cannot disagree about where a statement ends.
 */
export function splitStatements(command: string): Statement[] {
  const found: Statement[] = [];
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
