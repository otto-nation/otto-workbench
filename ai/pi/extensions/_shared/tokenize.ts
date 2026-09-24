/**
 * Tokenising a bash command, so the guards can ask about words rather than
 * about substrings of a line.
 *
 * The guards' rules were regexes over raw text, and two defect classes follow
 * from that directly. A separator inside quotes was read as a separator, so
 * `bash -c 'rm -rf x; echo done'` was cut into fragments that matched no rule
 * and the write was permitted. A `>` inside quotes was read as a redirect, so
 * `awk 'length > 80' f.txt` was refused as a write. Both are the same mistake
 * in opposite directions: quoting is what tells data from syntax, and a regex
 * over the line cannot see it.
 *
 * So the scan happens once, here, and every rule downstream reads the result.
 * `quoted` is the field that matters — a token carrying it is content the
 * command will pass along, never an operator the shell will act on.
 *
 * Deliberately not a shell parser. It resolves no variable, runs no expansion,
 * and understands no grammar beyond what separates one command from the next:
 * the guards ask "what command is this, and where does it write", and a parser
 * that answered more would be a larger surface for no more answer. What it
 * cannot represent it reports (see `Token.unparsed`) rather than guessing,
 * because a guess is what the old pattern scan was.
 *
 * Imports nothing, so tests/pi_extensions.bats can load it under plain `node`
 * the way it loads each detect.ts.
 */

/** The control operators that end one statement and begin the next. */
const OPERATORS = new Set([";", "&&", "||", "|", "&", "(", ")", "{", "}", ";;"]);

/** Redirect operators, longest first so `>>` is not read as two `>`. */
const REDIRECTS = [">>", "&>>", "&>", ">|", ">", "<<<", "<<", "<"];

export interface Token {
  /** The word with its quotes removed, which is what the shell passes along. */
  value: string;
  /** The word exactly as written, quotes and escapes intact. */
  raw: string;
  /**
   * True when any part of the word was quoted or escaped.
   *
   * The whole point of the scan. A quoted `>` is a filename, a quoted `;` is
   * an argument, and a rule that acts on either is the false-positive half of
   * what this file exists to fix.
   */
  quoted: boolean;
  /** True for an unquoted control or redirect operator. */
  operator: boolean;
  /**
   * Where the token starts in the line it was scanned from.
   *
   * Carried so a caller reassembling a span can take it from the original text
   * rather than by joining `raw` with spaces. Rejoining reshapes the command:
   * `2>&1` scans as four tokens and comes back as `2 > & 1`, which no rule
   * written against real shell text matches.
   */
  start: number;
  /** One past the token's last character in the line it was scanned from. */
  end: number;
  /**
   * True when the word ended in an unterminated quote.
   *
   * Reported rather than smoothed over: an unbalanced quote means the command
   * was cut somewhere this scan cannot see, and a caller that treats the
   * fragment as a whole word is reading something the shell never would.
   */
  unparsed: boolean;
}

function token(
  value: string, raw: string, quoted: boolean, start: number, end: number,
  { operator = false, unparsed = false } = {},
): Token {
  return { value, raw, quoted, operator, unparsed, start, end };
}

/**
 * The slice of `line` spanning `tokens`, exactly as it was written.
 *
 * The counterpart of the `start`/`end` fields: a caller that needs a run of
 * tokens back as text takes it from the source rather than rebuilding it, so
 * spacing, quoting and operator adjacency survive untouched.
 */
export function span(line: string, tokens: Token[]): string {
  if (tokens.length === 0) return "";
  return line.slice(tokens[0].start, tokens[tokens.length - 1].end);
}

/**
 * The index of the quote closing the one at `open`, or -1 when none does.
 *
 * A plain `indexOf` stops at the first matching character, which inside double
 * quotes is wrong: `"a\"b"` ends at the *last* quote, not the escaped one in
 * the middle. Reading it as the terminator splits the word early and leaves the
 * rest of the line to be rescanned as syntax — the same class of misreading as
 * the regex passes this file replaces, reintroduced one level down.
 *
 * Single quotes take no escapes at all, so a backslash inside them is content
 * and the first matching quote really is the terminator.
 */
function closingQuote(line: string, open: number): number {
  const quote = line[open];
  if (quote === "'") return line.indexOf(quote, open + 1);
  for (let i = open + 1; i < line.length; i++) {
    if (line[i] === "\\") {
      i++;
      continue;
    }
    if (line[i] === quote) return i;
  }
  return -1;
}

/**
 * Every token in `line`, with quoting resolved and operators marked.
 *
 * Single quotes take everything literally; double quotes take everything but a
 * backslash escape; a bare backslash escapes one character. That is the whole
 * of the quoting grammar the guards need — `$VAR` and `$(cmd)` are passed
 * through as ordinary characters, since no rule here resolves them and
 * pretending to would invite a caller to trust the result.
 */
export function tokenize(line: string): Token[] {
  const tokens: Token[] = [];
  let value = "";
  let raw = "";
  let quoted = false;
  let started = false;
  let unparsed = false;
  let begin = 0;

  const flush = (end: number) => {
    if (!started) return;
    tokens.push(token(value, raw, quoted, begin, end, { unparsed }));
    value = "";
    raw = "";
    quoted = false;
    started = false;
    unparsed = false;
  };

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];

    if (ch === "'" || ch === '"') {
      const close = closingQuote(line, i);
      if (!started) begin = i;
      started = true;
      quoted = true;
      if (close === -1) {
        // Unterminated: take the rest of the line as content and say so, rather
        // than letting the operators inside it read as syntax.
        value += line.slice(i + 1);
        raw += line.slice(i);
        unparsed = true;
        i = line.length;
        break;
      }
      let content = line.slice(i + 1, close);
      // A backslash escape is live inside double quotes and literal inside
      // single ones, which is the only way the two differ here.
      if (ch === '"') content = content.replace(/\\([\s\S])/g, "$1");
      value += content;
      raw += line.slice(i, close + 1);
      i = close;
      continue;
    }

    if (ch === "\\") {
      // An escaped character is content, including an escaped quote or space.
      // `\rm` is `rm` with `quoted` set, which is what stops the escape being a
      // way to spell a write the command-name rules do not recognise.
      if (!started) begin = i;
      if (i + 1 < line.length) {
        value += line[i + 1];
        raw += ch + line[i + 1];
        quoted = true;
        started = true;
        i++;
        continue;
      }
      value += ch;
      raw += ch;
      started = true;
      continue;
    }

    if (/\s/.test(ch)) {
      flush(i);
      continue;
    }

    // An operator ends the word before it and stands as a token of its own,
    // but only unquoted — a quoted one has already been consumed above.
    const op = matchOperator(line, i);
    if (op) {
      flush(i);
      tokens.push(token(op, op, false, i, i + op.length, { operator: true }));
      i += op.length - 1;
      continue;
    }

    if (!started) begin = i;
    value += ch;
    raw += ch;
    started = true;
  }
  flush(line.length);
  return tokens;
}

/**
 * The operator at `line[i]`, longest match first, or null.
 *
 * A leading file descriptor is *not* folded into the redirect: `2>&1` scans as
 * the word `2`, the operator `>`, the operator `&`, the word `1`. That is the
 * shape callers have to read, and it is deliberate — folding it would mean
 * deciding here which digits are descriptors and which are the tail of a word
 * like `curl7>`, and the rules downstream already have to tell `2>&1` (no
 * destination) from `2>file` (a destination) by looking at what follows.
 */
function matchOperator(line: string, i: number): string | null {
  for (const redirect of REDIRECTS) {
    if (line.startsWith(redirect, i)) return redirect;
  }
  for (const op of [";;", "&&", "||", ";", "|", "&", "(", ")"]) {
    if (line.startsWith(op, i)) return op;
  }
  // A brace is only a group when it stands as its own word: bash requires the
  // space in `{ cmd; }`, and everywhere else a brace is content. Splitting it
  // unconditionally broke `xargs -I{} rm {}`, whose `{}` is a placeholder.
  if ((line[i] === "{" || line[i] === "}") && standsAlone(line, i)) return line[i];
  return null;
}

/** True when the character at `i` is delimited by whitespace on both sides. */
function standsAlone(line: string, i: number): boolean {
  const before = i === 0 || /\s/.test(line[i - 1]);
  const after = i + 1 >= line.length || /\s/.test(line[i + 1]);
  return before && after;
}

/**
 * True when `tokens` holds an unparsed word.
 *
 * A caller deciding whether to refuse should consult this rather than reading
 * past it: an unbalanced quote is a command whose real shape is unknown, and
 * every verdict drawn from the fragments is drawn from something the shell
 * would not have run.
 */
export function hasUnparsed(tokens: Token[]): boolean {
  return tokens.some((t) => t.unparsed);
}
