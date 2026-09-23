/**
 * The write-capable-command and writable-path predicates for review-guard, kept
 * apart from the extension that uses them.
 *
 * Same split as the guards under ai/pi/extensions/: review-guard.ts imports
 * `isToolCallEventType` from the Pi SDK as a value, so it only loads inside a
 * Pi session. This file imports only node builtins and
 * ../extensions/_shared/statements.ts, which itself imports nothing, so
 * tests/pi_extensions.bats can run it under plain `node` and assert the shapes
 * it does and does not match.
 *
 * The path predicates (`canonical`, `within`, `isScratchPath`) are here for
 * that reason and no other — they were review-guard.ts's own until the write
 * gating needed a test, and a guard whose decisions can only be asserted by
 * grepping its source is one whose behaviour is not held by anything.
 */

import { realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, relative, resolve, sep } from "node:path";

import { statements } from "../extensions/_shared/statements.ts";

/**
 * Commands that write, matched at a statement head.
 *
 * Anchored rather than searched for anywhere in the command, because `\b`
 * treats `/` and `-` as word boundaries: an unanchored /\brm\b/ matches the
 * *path* in `cd /repo/isaac-fix-rm-stale && pytest`, and `\binstall\b` matches
 * any path with an `install/` segment. That disabled bash for whole review
 * sessions on nothing but a branch name. A write verb is only a write when it
 * is the command being run.
 */
const WRITE_COMMANDS = [
  "cp",
  "mv",
  "rm",
  "tee",
  "dd",
  "truncate",
  "install",
];

/**
 * A `git` subcommand reached past any global flags in front of it.
 *
 * `git -C /repo commit`, `git --no-pager commit` and `git -c user.name=x commit`
 * all run a commit, and a pattern anchored straight to `git\s+(?:commit|...)`
 * matches none of them — so the denial this list exists to enforce was a
 * one-flag rewrite away from doing nothing. That matters more here than a
 * missed `sed`: backend_claude.FIX_DENIED_TOOLS names the same subcommands and
 * says Pi enforces them by this mechanism, and the fix engine's accountability
 * rests on the claim. An agent that commits for itself lands work outside the
 * scope `fix.scope` watched it produce.
 *
 * `-c` and `-C` take a separate argument, so the value after them is consumed
 * rather than read as the subcommand — otherwise `git -C commit` would name a
 * directory and be treated as one.
 */
const GIT_WRITE_SUBCOMMANDS =
  /^\s*git\s+(?:(?:-[cC]\s+\S+|--(?:git-dir|work-tree|namespace|exec-path|config-env)(?:=\S*|\s+\S+)|-[a-zA-Z]+|--[a-zA-Z-]+)\s+)*(?:commit|push|checkout|switch|restore|reset|clean|stash|rebase|merge|apply|am|cherry-pick|revert)\b/;

/**
 * Write constructs that are not a bare command name, matched against each
 * statement rather than the whole command string.
 *
 * Anchored to a *flag position*, not to "the letter appears somewhere". The
 * first spelling of the sed rule was /^\s*sed\s+[^|]*-i/, and `[^|]*` crosses
 * into the arguments: a read-only `sed -n '1,5p' doc-internal/CLAUDE.md` was
 * refused because the path contains `-i`, as was any `s///` expression carrying
 * one. 18 tracked paths in this repo trip that, and the directory under review
 * when it was found was named `doc-internal`, so it fired on nearly every read
 * the agent attempted against its own subject tree. This is the same bug the
 * WRITE_COMMANDS comment above describes for `\b`, which had already cost a
 * session once; the reasoning had not been carried across to these patterns,
 * and neither had a negative-case test.
 */
const WRITE_STATEMENT_PATTERNS = [
  // `-i` as a flag: alone, in a cluster (`-ni`), with a suffix (`-i.bak`), or
  // spelled out. `g?sed` because GNU sed is `gsed` on macOS and edits in place
  // just the same.
  /^\s*(?:g?sed|perl)\s+(?:[^|]*\s)?-(?:[a-zA-Z]*i|-in-place)\b/,
  // The long spellings are not optional extras: `curl --output f` and
  // `wget --output-document f` write a file and matched nothing before.
  /^\s*(?:curl|wget)\s+(?:[^|]*\s)?(?:-[a-zA-Z]*[oO]\b|--output(?:-document)?\b|--remote-name\b)/,
  // `apply` and `am` write arbitrary file content straight out of a patch,
  // which is the shape a fix pass reaches for when it wants a diff on disk.
  GIT_WRITE_SUBCOMMANDS,
];

/**
 * Command wrappers that take another command as their argument.
 *
 * `commandHead` reads one word, so every one of these hid the write behind it:
 * `sudo rm -rf x`, `xargs rm -f`, `env rm -rf x` and `time rm -rf x` were all
 * allowed while the bare `rm` was refused. A guard that blocks the ergonomic
 * spelling of a write and permits the awkward one is not containing anything —
 * it is charging the agent turns to discover the rewrite.
 *
 * Skipping the wrapper's own flags is what makes this work on `xargs -0 rm`,
 * and `WRAPPER_VALUE_FLAGS` is what keeps that from going wrong in the unsafe
 * direction: a flag taking a separate value leaves the *value* sitting where
 * the command should be, so `sudo -u root rm -rf x` reads its command as `root`
 * and is allowed. Skipping value and flag together is the difference between
 * this failing closed and failing open.
 */
const COMMAND_WRAPPERS = new Set(["sudo", "env", "time", "xargs", "nohup", "nice", "doas"]);

/**
 * Wrapper flags that consume the word after them.
 *
 * Keyed by wrapper, because the same letter means different things: `-u` drops
 * a variable for `env` and names a user for `sudo`. A flag written with an
 * attached value (`-n5`, `--user=root`) consumes nothing extra and is skipped
 * by the ordinary flag rule.
 *
 * ceiling: a hand-listed set, so a value-taking flag absent from it reads its
 * value as the command name and the statement is allowed. The wrappers here
 * are the ones an agent plausibly reaches for and the list covers their common
 * flags; the containment that does not depend on it is that the fix engine
 * commits by scope rather than trusting the agent. Upgrade to a real option
 * parser if a wrapper invocation ever gets past this that mattered.
 */
const WRAPPER_VALUE_FLAGS: Record<string, Set<string>> = {
  sudo: new Set(["-u", "-g", "-p", "-C", "-U", "-T", "-r", "-t", "--user", "--group"]),
  doas: new Set(["-u", "-C"]),
  env: new Set(["-u", "-C", "-S", "--unset", "--chdir"]),
  time: new Set(["-f", "-o", "--format", "--output"]),
  nice: new Set(["-n", "--adjustment"]),
  xargs: new Set(["-I", "-L", "-n", "-P", "-s", "-E", "-a", "-d", "-i", "--replace",
                  "--max-args", "--max-procs", "--arg-file", "--delimiter"]),
  nohup: new Set(),
};

/**
 * A redirect that names a destination, which `2>&1` and `2>/dev/null` do not.
 *
 * Matching a bare `>` instead caught every `cmd 2>&1` an agent writes while
 * reading, and a guard that fires on ordinary reads is one whose refusals stop
 * being read.
 */
const REDIRECT = />>?\s*(?!&\d)(?!\/dev\/(?:null|stdout|stderr)\b)(\S+)/g;

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

function isScratchTarget(target: string): boolean {
  const cleaned = target.replace(/^['"]|['"]$/g, "");
  if (cleaned === "/dev/null") return true;
  return SCRATCH_PREFIXES.some((prefix) => cleaned.startsWith(prefix));
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
 * so it created one inside — and then could not remove it, because `rm`, `mv`,
 * `cp` and `git clean` are all refused. That is not containment. `changed_files`
 * in ai/lib/fix/scope.py counts untracked files, so every abandoned scratch file
 * was inside the commit scope and was committed and pushed with the fix; one
 * observed run left seven.
 */
export function isScratchPath(path: string): boolean {
  return SCRATCH_PREFIXES.some((prefix) => within(prefix, path));
}

/** The leading command word of a statement, with env assignments skipped. */
function commandHead(statement: string): string {
  return unwrap(statement).split(/\s+/).filter(Boolean)[0]?.split("/").pop() ?? "";
}

/**
 * `statement` with env assignments and command wrappers taken off the front.
 *
 * What is left is the command actually being run, which is what every rule
 * below wants to match against: `sudo sed -i ... f` is an in-place edit, and a
 * pattern anchored with `^\s*sed` sees the `sudo` and declines.
 */
function unwrap(statement: string): string {
  const words = statement.trim().split(/\s+/).filter(Boolean);
  // The wrapper whose flags are currently being skipped, so `-u` is read
  // against the right one. Empty until a wrapper has been seen.
  let wrapper = "";
  for (let i = 0; i < words.length; i++) {
    const word = words[i];
    // `FOO=bar cmd` — an assignment prefix is not the command being run.
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(word)) continue;
    // Strip a path so `/bin/rm` and `rm` read the same.
    const name = word.split("/").pop() ?? "";
    if (COMMAND_WRAPPERS.has(name)) {
      wrapper = name;
      continue;
    }
    // A wrapper's own flags belong to the wrapper, not to the command it runs.
    if (word.startsWith("-")) {
      // A value written separately is the flag's, not the command being run.
      if (WRAPPER_VALUE_FLAGS[wrapper]?.has(word)) i++;
      continue;
    }
    return words.slice(i).join(" ");
  }
  return "";
}

/**
 * `statement` with quoted spans blanked, for the flag patterns to match against.
 *
 * A flag letter inside a quoted argument is data, not a flag: `curl -s URL -H
 * 'A: -o'` is a read, and `sed -n "s/a-i/b/p" f` is a read. The same two passes
 * ai/claude/bin/claude-bash-guard makes, and they inherit the same ceiling it
 * documents — an escaped quote ends a span early and an unpaired apostrophe
 * re-pairs with a later one. Replaced with a placeholder rather than deleted,
 * so `sed -i '' s/a/b/ f` keeps an argument where the empty string was and the
 * words on either side do not run together.
 *
 * ceiling: two regex passes, not a tokenizer, so both misreadings above are
 * possible. Each costs at most one refused or one permitted statement in a
 * review session, and the redirect rule below is unaffected because it reads
 * the raw statement. Upgrade to a real tokenizer if either misfire shows up on
 * a command worth running.
 */
function blankQuoted(statement: string): string {
  return statement.replace(/'[^']*'/g, "QUOTEDARG").replace(/"[^"]*"/g, "QUOTEDARG");
}

/** A `sh -c "..."` payload, which is a command in its own right. */
const SHELL_DASH_C = /^(?:sh|bash|zsh|dash|ksh)\s+(?:-[a-zA-Z]*\s+)*-[a-zA-Z]*c\s+(.*)$/s;

/**
 * The command inside a `sh -c "..."` wrapper, or null when there is none.
 *
 * `bash -c 'rm -rf x'` was allowed while a bare `rm -rf x` was refused, so the
 * wrapper was a complete bypass of every rule here. Unwrapped and rescanned
 * rather than blocked outright: a payload that only reads is still a read, and
 * a guard that refuses every `-c` would be refusing on the shape of the command
 * rather than on what it does.
 */
function shellPayload(statement: string): string | null {
  const match = SHELL_DASH_C.exec(unwrap(statement));
  if (!match) return null;
  const payload = match[1].trim();
  // An unbalanced quote means the split above cut through a quoted span, so the
  // payload is a fragment. Strip a matched pair only.
  const quoted = /^(['"])([\s\S]*)\1$/.exec(payload);
  return quoted ? quoted[2] : payload;
}

/**
 * Why `command` may not run in a review session, or null when it may.
 *
 * Returns the offending statement so the refusal can name it, rather than
 * echoing a 120-character slice of the whole command — a truncated summary hid
 * the trailing redirect that was the actual match, and the refusal read as
 * though it had blocked the `cd` in front of it.
 */
export function blockedWriteCommand(command: string, depth = 0): string | null {
  for (const statement of statements(command)) {
    if (!statement.trim()) continue;

    const head = commandHead(statement);
    if (WRITE_COMMANDS.includes(head)) {
      return `\`${head}\` writes: ${statement.trim()}`;
    }

    // Depth-limited so a pathological `sh -c "sh -c ..."` cannot spin. One
    // level is every real invocation; the limit is about termination, not about
    // a nesting an agent is expected to reach.
    const payload = depth < 4 ? shellPayload(statement) : null;
    if (payload && blockedWriteCommand(payload, depth + 1)) {
      return `write-capable command inside a shell wrapper: ${statement.trim()}`;
    }

    // Against the unwrapped, quote-blanked statement: `sudo sed -i` and
    // `xargs rm` are the writes they wrap, and a flag letter inside a quoted
    // argument is data rather than a flag.
    const unwrapped = blankQuoted(unwrap(statement));
    for (const pattern of WRITE_STATEMENT_PATTERNS) {
      if (pattern.test(unwrapped)) return `write-capable command: ${statement.trim()}`;
    }

    // A statement can carry more than one redirect (e.g. `cmd > a.txt 2>b.txt`),
    // and each one is a separate write target — matchAll so a scratch first
    // redirect does not shadow a non-scratch second one.
    for (const redirect of statement.matchAll(REDIRECT)) {
      if (!isScratchTarget(redirect[1])) {
        return `redirect writes to ${redirect[1]}: ${statement.trim()}`;
      }
    }
  }
  return null;
}
