/**
 * What review-guard refuses, and where it lets a write land.
 *
 * NOT a security boundary, and the naming here is deliberate about that. A fix
 * agent holds an unrestricted `bash` (see PI_FIX_TOOLS in backend_pi.py), so
 * `python3 -c "os.remove(f)"`, `find -delete`, `make clean` and every script in
 * the repo write freely and are permitted by design. A predicate that reads
 * command strings cannot close that and should not pretend to: an earlier
 * version of this file grew five lists of write verbs, shells and wrappers
 * trying, and blocked only the spellings an agent reaches for by accident while
 * missing every one it would reach for on purpose.
 *
 * What actually contains a fix agent is elsewhere, and none of it depends on
 * this file:
 *
 *   - `write` and `edit` are gated on their path, in review-guard.ts, which a
 *     command spelling cannot route around
 *   - the engine commits only the paths it watched the agent touch
 *     (`fix.scope.agent_changed`), so stray writes are never staged
 *   - the push consults the publishing gate (`land.land(gated=True)`)
 *
 * So this is left with the one job those three cannot do, and it is a narrow
 * one: keep the engine's scoped commit the *only* commit. An agent that runs
 * `git commit` itself lands work outside the scope `fix.scope` watched, under a
 * message nothing in this codebase wrote — and that is invisible to all three
 * mechanisms above, because by the time they look the work is already in a
 * commit. The redirect rule is here for the same reason in miniature: a
 * redirect names a destination, so it is one of the few shapes where the text
 * really does say where the write goes.
 *
 * Read the refusals as ergonomics with one exception, not as enforcement with
 * gaps. They steer an agent toward the tools the pipeline can account for.
 *
 * Same split as the guards under ai/pi/extensions/: review-guard.ts imports
 * `isToolCallEventType` from the Pi SDK as a value, so it only loads inside a
 * Pi session. This file imports only node builtins, ../extensions/_shared, and
 * so runs under plain `node` — which is what lets tests/pi_extensions.bats
 * assert its behaviour rather than grep its source.
 */

import { realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, relative, resolve, sep } from "node:path";

import { statements } from "../extensions/_shared/statements.ts";
import { hasUnparsed, tokenize, type Token } from "../extensions/_shared/tokenize.ts";

/**
 * `git` subcommands that move the branch, the index or the worktree.
 *
 * The one rule in this file that is enforcement rather than ergonomics. The
 * engine commits for the agent, scoped to the paths it watched
 * (`fix.engine.run` → `git.land.land`); an agent that commits, rebases or
 * resets is not finishing the job early, it is landing work outside the only
 * scope the pass can account for. `backend_claude.FIX_DENIED_TOOLS` denies the
 * same subcommands through Claude's matcher, and the two are kept in step —
 * tests/test_ai_backend_observability.py asserts the parity by running this
 * predicate against every command that list denies.
 *
 * Compared against a whole token, never matched as a prefix. A `\b` after the
 * subcommand made `merge` match `merge-base`, and `git merge-base` is how a
 * review establishes its own base — ai/lib calls it in fifteen places.
 *
 * Kept to subcommands an agent plausibly runs. The porcelain's long tail
 * (`filter-branch`, `gc`, `prune`, `update-ref`, …) is absent: a list
 * enumerates members rather than fixing a class, so it earns entries one
 * observed failure at a time.
 */
const GIT_WRITE_SUBCOMMANDS = new Set([
  "commit", "push", "checkout", "switch", "restore", "reset", "clean", "stash",
  "rebase", "merge", "apply", "am", "cherry-pick", "revert",
  "add", "rm", "mv", "branch", "tag", "config", "worktree", "fetch", "pull",
]);

/**
 * `git` global flags that take their value as a separate word.
 *
 * Consumed with their value so `git -C commit` reads as a directory named
 * `commit` rather than as the subcommand.
 */
const GIT_VALUE_FLAGS = new Set(["-c", "-C", "--git-dir", "--work-tree",
                                 "--namespace", "--exec-path", "--config-env"]);

/**
 * Flags that make a mutating subcommand read-only.
 *
 * `git apply --check`, `git clean -n` and `git push --dry-run` report what
 * they would do and change nothing, and refusing them cost a review the
 * cheapest way to answer its own questions.
 */
const GIT_DRY_RUN_FLAGS = new Set(["--dry-run", "-n", "--check", "--stat",
                                   "--numstat", "--summary", "--help"]);

/**
 * Subcommand pairs that only read, despite the first word being a write verb.
 *
 * `git stash list` and `git branch --list` are reads; `git stash` and
 * `git branch -D` are not. Keyed on the second word, which is the only thing
 * that tells them apart.
 */
const GIT_READ_ONLY_ACTIONS: Record<string, Set<string>> = {
  stash: new Set(["list", "show"]),
  branch: new Set(["--list", "-l", "--show-current", "-v", "--verbose"]),
  tag: new Set(["--list", "-l"]),
  config: new Set(["--get", "--get-all", "--list", "-l", "--get-regexp"]),
  worktree: new Set(["list"]),
};

/**
 * Why this `git` invocation writes, or null when it only reads.
 *
 * Token-based because every previous spelling of this rule failed on the
 * boundary between a subcommand and the text around it: anchored to `git\s+`
 * it missed `git -C /r commit`, and loosened with `\b` it caught
 * `git merge-base`.
 */
function gitWrite(tokens: Token[]): string | null {
  if (tokens[0]?.value.split("/").pop() !== "git") return null;
  let i = 1;
  while (i < tokens.length) {
    const word = tokens[i].value;
    if (!word.startsWith("-")) break;
    // `--git-dir=/x` carries its value; `--git-dir /x` takes the next word.
    i += GIT_VALUE_FLAGS.has(word) ? 2 : 1;
  }
  const subcommand = tokens[i]?.value;
  if (!subcommand || !GIT_WRITE_SUBCOMMANDS.has(subcommand)) return null;

  const rest = tokens.slice(i + 1).map((t) => t.value);
  if (rest.some((word) => GIT_DRY_RUN_FLAGS.has(word))) return null;
  if (rest[0] && GIT_READ_ONLY_ACTIONS[subcommand]?.has(rest[0])) return null;
  return subcommand;
}

/** Redirect operators that name a destination file. */
const WRITING_REDIRECTS = new Set([">", ">>", ">|", "&>", "&>>"]);

/** Destinations that discard, so a redirect naming one writes nothing. */
const NULL_SINKS = new Set(["/dev/null", "/dev/stdout", "/dev/stderr"]);

/**
 * Every file a statement's redirects would write, as scanned tokens.
 *
 * Read from the token scan rather than from a regex over the raw statement,
 * which is what the rule did before. A `>` inside quotes is an argument, and
 * matching it as a redirect refused a large share of ordinary reads:
 * `awk 'length > 80' f.txt`, `grep -rn 'a->b' src/`, `rg 'fn f() -> R' src/`
 * and any `jq` with a comparison in it. A review agent greps constantly, and a
 * guard whose refusals land on greps is one whose refusals stop being read.
 *
 * A descriptor duplication (`2>&1`, `>&2`) names no file: the tokenizer emits
 * `&` as its own operator, so the destination slot holds an operator rather
 * than a word and there is nothing to report.
 */
function redirectTargets(tokens: Token[]): string[] {
  const targets: string[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    if (!tok.operator || !WRITING_REDIRECTS.has(tok.value)) continue;
    const target = tokens[i + 1];
    // `> &1` is a descriptor, and a redirect with nothing after it is a syntax
    // error rather than a write.
    if (!target || target.operator) continue;
    if (NULL_SINKS.has(target.value)) continue;
    targets.push(target.value);
  }
  return targets;
}

/**
 * Scratch destinations a redirect may target.
 *
 * The same list ai/claude/bin/claude-bash-guard exempts, for the same reason,
 * and the two must stay in step: test-pipe-guard refuses a piped suite and
 * prescribes `pytest ... > /tmp/out.txt 2>&1` as the fix. Without this, that
 * prescribed rewrite was itself refused here, and a review agent that obeyed
 * the first refusal could not satisfy the second. Two guards whose remedies
 * contradict each other leave no command the agent can run, which is how a
 * review burned two full turn budgets and wrote nothing.
 */
export const SCRATCH_PREFIXES = ["/tmp/", "/private/tmp/", "/var/folders/"];

/**
 * True for a redirect destination the agent may write.
 *
 * Delegates to `isScratchPath`, which canonicalises. The raw `startsWith` this
 * replaces disagreed with that function about the same prefix list, so
 * `> /private/tmp/../../Users/x/p.txt` was accepted as scratch by the redirect
 * rule and rejected as outside the worktree by the write tool — one file, two
 * predicates, opposite answers.
 *
 * Absolute paths only. `isScratchPath` resolves what it is given against the
 * process cwd, so every *relative* target came back scratch whenever that cwd
 * happened to sit under one of the prefixes — and a review runs in a worktree
 * under /var/folders often enough for that to be the common case, not the
 * corner. A redirect the guard cannot place is a redirect it must not exempt.
 *
 * `~` and `$HOME` are unexpanded here because nothing in this file expands
 * them, so they are not absolute either and fall to the same refusal. That is
 * the right answer for a different reason: `> ~/notes.txt` writes to the home
 * directory, which is not scratch however it is spelled.
 *
 * The target arrives dequoted from the token scan, so no quote stripping is
 * needed.
 */
function isScratchTarget(target: string): boolean {
  if (NULL_SINKS.has(target)) return true;
  if (!target.startsWith("/")) return false;
  return isScratchPath(target);
}

/**
 * `path` with every symlink in it resolved, as far as the filesystem allows.
 *
 * resolve() normalises `.` and `..` but does not follow symlinks, and on macOS
 * /tmp is a symlink to /private/tmp: a root and a path that name the same
 * directory in different spellings compare as unrelated, and the write is
 * refused. A path that does not exist yet — which every new file is — has no
 * realpath, so the nearest existing ancestor is canonicalised instead and the
 * remainder appended.
 *
 * Here rather than in review-guard.ts so the write gating is testable: that
 * file imports the Pi SDK as a value and only loads inside a session, while
 * this one is loadable under plain `node`. Nothing below imports the SDK.
 */
export function canonical(path: string): string {
  let head = resolve(path);
  const tail: string[] = [];
  for (;;) {
    try {
      return resolve(realpathSync(head), ...tail);
    } catch {
      const parent = dirname(head);
      // dirname("/") is "/": the root itself does not resolve, so give up and
      // fall back to the lexical form rather than looping forever.
      if (parent === head) return resolve(path);
      tail.unshift(basename(head));
      head = parent;
    }
  }
}

/** True when `path` is inside `root` — the same directory, or below it. */
export function within(root: string, path: string): boolean {
  const resolvedRoot = resolve(root);
  const rel = relative(canonical(resolvedRoot), canonical(resolve(resolvedRoot, path)));
  // A bare startsWith("..") also rejects a sibling-named child such as
  // `...hidden`, which is inside the root.
  if (rel === "") return true;
  return rel !== ".." && !rel.startsWith(".." + sep) && !isAbsolute(rel);
}

/**
 * True for a path under a scratch root, which write and edit may target.
 *
 * `isScratchTarget` above has always exempted these for a *redirect*, and
 * withholding them from the write tool left the guard contradicting itself: an
 * agent that needed a scratch file could not create one outside the worktree,
 * so it created one inside — and then could not remove it. `changed_files` in
 * ai/lib/fix/scope.py counts untracked files, so every abandoned scratch file
 * was inside the commit scope and was committed and pushed with the fix; one
 * observed run left seven.
 */
export function isScratchPath(path: string): boolean {
  return SCRATCH_PREFIXES.some((prefix) => within(prefix, path));
}

/** Grouping operators that precede a command rather than being one. */
const GROUPING = new Set(["(", "{"]);

/**
 * The tokens of the command actually being run, env assignments stripped.
 *
 * Reads the token scan, so the command name is the word the shell would run
 * rather than the characters that spell it: `'git' commit` and `\git commit`
 * both run git, and both read as an unknown command to anything matching raw
 * text.
 *
 * A leading `(` or `{` is stepped over for the same reason — `(git commit)`
 * runs git in a subshell, and the parenthesis is syntax rather than the
 * command.
 */
function commandTokens(statement: string): Token[] {
  const tokens = tokenize(statement);
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    if (tok.operator && GROUPING.has(tok.value)) continue;
    if (tok.operator) break;
    // `FOO=bar cmd` — an assignment prefix is not the command being run.
    if (!tok.quoted && /^[A-Za-z_][A-Za-z0-9_]*=/.test(tok.value)) continue;
    return tokens.slice(i);
  }
  return [];
}

/**
 * Why `command` may not run in a review session, or null when it may.
 *
 * Two things only: a `git` subcommand that would commit outside the engine's
 * scope, and a redirect to a destination that is not scratch. See the module
 * header for why the list is this short and why `python3 -c` is not on it.
 *
 * Returns the offending statement so the refusal can name it, rather than
 * echoing a 120-character slice of the whole command — a truncated summary hid
 * the trailing redirect that was the actual match, and the refusal read as
 * though it had blocked the `cd` in front of it.
 */
export function bypassesTheCommitScope(command: string): string | null {
  // An unbalanced quote means the scan cannot see where the command ends, so
  // both rules below would be reading fragments. Against the whole command,
  // not each statement: `statements()` splits on newlines, so a quoted string
  // spanning two lines arrives already torn into halves that each look
  // unbalanced.
  if (hasUnparsed(tokenize(command))) {
    return `unbalanced quote, so the command cannot be read: ${command.trim()}`;
  }

  for (const statement of statements(command)) {
    if (!statement.trim()) continue;

    const subcommand = gitWrite(commandTokens(statement));
    if (subcommand) {
      return `\`git ${subcommand}\` writes: ${statement.trim()}`;
    }

    // A statement can carry more than one redirect (e.g. `cmd > a.txt 2>b.txt`),
    // and each one is a separate write target — every one is checked so a
    // scratch first redirect does not shadow a non-scratch second one.
    for (const target of redirectTargets(tokenize(statement))) {
      if (!isScratchTarget(target)) {
        return `redirect writes to ${target}: ${statement.trim()}`;
      }
    }
  }
  return null;
}
