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
import { span, tokenize, type Token } from "../extensions/_shared/tokenize.ts";

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
  // Creating, removing and linking are writes as much as copying is; the
  // original list named only the verbs that move file *contents* around, so a
  // fix agent could create or delete a path freely.
  "touch",
  "mkdir",
  "rmdir",
  "ln",
  "unlink",
  "shred",
  // Metadata is content too: a mode or owner change is a tree modification a
  // review is not entitled to make.
  "chmod",
  "chown",
  "chgrp",
  // Each of these writes a tree from an archive or another tree, which is the
  // largest write shape available and was the least guarded.
  "rsync",
  "patch",
  "tar",
  "unzip",
];

/**
 * `git` subcommands that move the branch, the index or the worktree.
 *
 * backend_claude.FIX_DENIED_TOOLS names the same set and says Pi enforces them
 * by this mechanism, and the fix engine's accountability rests on the claim:
 * an agent that commits for itself lands work outside the scope `fix.scope`
 * watched it produce.
 *
 * Compared against a whole token, never matched as a prefix. A `\b` after the
 * subcommand made `merge` match `merge-base` — the same hyphen-boundary bug
 * the WRITE_COMMANDS comment describes, and an expensive one: `git merge-base`
 * is how a review establishes its own base, and this repo's `ai/lib/` calls it
 * in fifteen places.
 */
const GIT_WRITE_SUBCOMMANDS = new Set([
  "commit", "push", "checkout", "switch", "restore", "reset", "clean", "stash",
  "rebase", "merge", "apply", "am", "cherry-pick", "revert",
  // Absent before, and each writes: `git rm -rf .` deletes the worktree and
  // stages the deletion while a bare `rm -rf .` is refused.
  "add", "rm", "mv", "branch", "tag", "config", "update-ref", "symbolic-ref",
  "worktree", "notes", "replace", "filter-branch", "gc", "prune",
  "sparse-checkout", "submodule", "bisect", "fetch", "pull", "remote", "init",
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
  remote: new Set(["show", "get-url", "-v", "--verbose"]),
  notes: new Set(["list", "show"]),
  config: new Set(["--get", "--get-all", "--list", "-l", "--get-regexp"]),
  submodule: new Set(["status", "summary", "foreach"]),
  worktree: new Set(["list"]),
  bisect: new Set(["log", "view"]),
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

/**
 * Commands that write only when a particular flag is present, by command name.
 *
 * A flag is looked for among the *tokens*, so it is a flag when the shell
 * would read it as one and an argument otherwise. The first spelling of the
 * sed rule was /^\s*sed\s+[^|]*-i/, and `[^|]*` crosses into the arguments: a
 * read-only `sed -n '1,5p' doc-internal/CLAUDE.md` was refused because the
 * path contains `-i`, as was any `s///` expression carrying one. 18 tracked
 * paths in this repo trip that. Quoting settles both directions now —
 * `sed -e "s/a/it's/" -i '' f` really does edit in place and is refused,
 * `sed -n "s/a-i/b/p" f` does not and is not.
 *
 * `g?sed` because GNU sed is `gsed` on macOS and edits in place just the same.
 */
const FLAG_WRITES: { commands: Set<string>; writes: (flag: string) => boolean }[] = [
  {
    commands: new Set(["sed", "gsed", "perl"]),
    // `-i` alone, in a cluster (`-ni`), with a suffix (`-i.bak`), or spelled out.
    writes: (flag) =>
      flag === "--in-place" ||
      (/^-[a-zA-Z]*i/.test(flag) && !flag.startsWith("--")),
  },
  {
    commands: new Set(["curl", "wget"]),
    // The long spellings are not optional extras: `curl --output f` and
    // `wget --output-document f` write a file and matched nothing before.
    writes: (flag) =>
      flag === "--output" || flag === "--output-document" ||
      flag === "--remote-name" ||
      (/^-[a-zA-Z]*[oO]/.test(flag) && !flag.startsWith("--")),
  },
];

/**
 * Why this command writes by virtue of a flag, or null when it does not.
 *
 * A destination of `/dev/null` or a scratch path is not a write, which is what
 * keeps `curl -so /dev/null -w '%{http_code}' https://x` — the standard
 * status-code probe — out of the refusals, alongside the `curl -o /tmp/x`
 * that the redirect rule has always permitted in its own spelling.
 */
function flagWrite(tokens: Token[]): string | null {
  const name = tokens[0]?.value.split("/").pop() ?? "";
  const rule = FLAG_WRITES.find((r) => r.commands.has(name));
  if (!rule) return null;
  for (let i = 1; i < tokens.length; i++) {
    const tok = tokens[i];
    if (tok.quoted || tok.operator || !rule.writes(tok.value)) continue;
    // An attached destination (`-oFILE`) or the next word, whichever it is.
    const attached = tok.value.replace(/^-[a-zA-Z]*[oO]/, "");
    const destination = attached || tokens[i + 1]?.value;
    if (destination && isScratchTarget(destination)) continue;
    return tok.value;
  }
  return null;
}

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
const COMMAND_WRAPPERS = new Set([
  "sudo", "env", "time", "xargs", "nohup", "nice", "doas",
  // Same shape, and each was a one-word prefix that hid every write behind it:
  // `exec rm -rf x` and `timeout 10 rm -rf x` were both permitted.
  "exec", "command", "timeout", "stdbuf", "setsid", "builtin",
]);

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
  // `-S` is deliberately absent, unlike every other value-taking flag here:
  // `env -S 'rm -f x'` takes a whole *command* as its value, so skipping the
  // value would skip the write. Left unlisted, the value lands where the
  // command is expected and is scanned as one, which is the correct reading.
  sudo: new Set(["-u", "-g", "-p", "-C", "-U", "-T", "-r", "-t", "--user", "--group",
                 "-D", "--chdir", "-R", "--chroot", "-h", "--host", "--prompt",
                 "--close-from"]),
  doas: new Set(["-u", "-C"]),
  env: new Set(["-u", "-C", "--unset", "--chdir"]),
  time: new Set(["-f", "-o", "--format", "--output"]),
  nice: new Set(["-n", "--adjustment"]),
  timeout: new Set(["-s", "--signal", "-k", "--kill-after"]),
  stdbuf: new Set(["-i", "-o", "-e", "--input", "--output", "--error"]),
  xargs: new Set(["-I", "-L", "-n", "-P", "-s", "-E", "-a", "-d", "-i", "--replace",
                  "--max-args", "--max-procs", "--arg-file", "--delimiter"]),
  nohup: new Set(),
};

/**
 * Redirect operators that name a destination file.
 *
 * `<` and `<<<` read rather than write, and `2>&1` names a descriptor rather
 * than a file — both are told apart by what follows the operator, not by the
 * operator itself, so the check lives in `redirectTargets` below.
 */
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
  return commandTokens(statement)[0]?.value.split("/").pop() ?? "";
}

/** Grouping operators that precede a command rather than being one. */
const GROUPING = new Set(["(", "{"]);

/**
 * The tokens of the command actually being run, wrappers and env stripped.
 *
 * Reads the token scan, so the command name is the word the shell would run
 * rather than the characters that spell it. `'rm' -rf x` and `\rm -rf x` both
 * run rm, and both read as an unknown command to anything matching raw text —
 * a one-character rewrite of the refused form, which is the class of hole the
 * COMMAND_WRAPPERS comment says a guard must not leave open.
 *
 * A leading `(` or `{` is stepped over for the same reason: `(rm -rf x)` runs
 * rm in a subshell, and the parenthesis is syntax rather than the command.
 */
function commandTokens(statement: string): Token[] {
  const tokens = tokenize(statement);
  let wrapper = "";
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    // A subshell or brace group opens with an operator that is not a command.
    if (tok.operator && GROUPING.has(tok.value)) continue;
    if (tok.operator) break;
    // `FOO=bar cmd` — an assignment prefix is not the command being run.
    if (!tok.quoted && /^[A-Za-z_][A-Za-z0-9_]*=/.test(tok.value)) continue;
    const name = tok.value.split("/").pop() ?? "";
    if (COMMAND_WRAPPERS.has(name)) {
      wrapper = name;
      continue;
    }
    // `timeout 10 rm -rf x` — the duration is positional, not a flag, so the
    // flag-skipping below never reaches it and `10` reads as the command.
    if (wrapper === "timeout" && /^[\d.]+[smhd]?$/.test(tok.value)) continue;
    // A wrapper's own flags belong to the wrapper, not the command it runs.
    // Quoted, they are an argument: `sudo '-u'` is not sudo's flag.
    if (!tok.quoted && tok.value.startsWith("-")) {
      if (WRAPPER_VALUE_FLAGS[wrapper]?.has(tok.value)) i++;
      continue;
    }
    return tokens.slice(i);
  }
  return [];
}

/**
 * `statement` with env assignments and command wrappers taken off the front.
 *
 * What is left is the command actually being run, which is what every rule
 * below wants to match against: `sudo sed -i ... f` is an in-place edit, and a
 * pattern anchored with `^\s*sed` sees the `sudo` and declines.
 */
function unwrap(statement: string): string {
  const tokens = commandTokens(statement);
  // Sliced from the source, so the flag patterns match the text as written.
  return tokens.length ? span(statement, tokens) : "";
}

/**
 * The last command wrapper `unwrap` was holding when it ran out of words to
 * unwrap, or "" when `unwrap` found a residual command instead.
 *
 * Mirrors `unwrap`'s own loop rather than calling it, because `unwrap` throws
 * the wrapper name away once it returns "" — the exact case this exists for.
 * `env sudo -s` and `sudo -s` both unwrap to "": `env` is skipped as a
 * wrapper, `sudo` is then seen and also skipped as a wrapper (it is in
 * `COMMAND_WRAPPERS` too), and `-s` is not a value flag for `sudo` so it is
 * skipped as an ordinary wrapper flag, leaving no residual word. This is what
 * tells the two apart from a wrapper around an ordinary command that happens
 * to produce no residual for some other reason.
 */
function lastWrapper(statement: string): string {
  const words = statement.trim().split(/\s+/).filter(Boolean);
  let wrapper = "";
  for (let i = 0; i < words.length; i++) {
    const word = words[i];
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(word)) continue;
    const name = word.split("/").pop() ?? "";
    if (COMMAND_WRAPPERS.has(name)) {
      wrapper = name;
      continue;
    }
    if (word.startsWith("-")) {
      if (WRAPPER_VALUE_FLAGS[wrapper]?.has(word)) i++;
      continue;
    }
    return "";
  }
  return wrapper;
}

/**
 * Commands that hand the agent a shell this predicate cannot read.
 *
 * `sudo`, `doas` and `su` elevate into one; the rest are the shell itself. A
 * `sh -c "..."` is not here — its payload is a command in its own right and is
 * unwrapped and rescanned below, which is why the check that uses this set
 * excludes a statement carrying one.
 *
 * ceiling: an interpreter with an inline-eval flag is the same hole in a
 * different language — `python3 -c "import os; os.remove(f)"` is how an agent
 * escaped this guard once already, and node, perl and ruby all offer it. Not
 * closed here because an interpreter is also the ordinary way to run a test or
 * a one-liner that only reads, so refusing the command name would refuse the
 * common case, and reading the payload means parsing four more languages.
 * Upgrade when a refusal log shows an agent reaching for one to write with.
 */
const INTERACTIVE_SHELLS = new Set([
  "sudo", "doas", "su", "sh", "bash", "zsh", "dash", "ksh", "fish",
  // csh and tcsh are shells on the same terms; rbash is a restricted bash,
  // which is still a shell this predicate cannot read.
  "csh", "tcsh", "rbash",
]);

/**
 * A `sh -c "..."` payload, which is a command in its own right.
 *
 * The shell name is matched after `unwrap` has stripped any leading path, so
 * `/bin/sh -c` and `sh -c` read the same — they did not until the escape rule
 * below started refusing what this cannot parse, and a path-qualified `-c` was
 * then refused as an interactive shell.
 *
 * Long flags and a `-c` that is not last in its cluster are both accepted
 * (`bash --norc -c`, `bash -ce`), because everything this fails to parse falls
 * through to that escape rule: an unparsed spelling is not an unrecognised
 * read, it is a refusal. `fish` is in the alternation for the same reason — it
 * is in INTERACTIVE_SHELLS, so omitting it here refuses `fish -c 'pytest'`.
 */
const SHELL_DASH_C =
  /^(?:sh|bash|zsh|dash|ksh|fish)\s+(?:(?:-[a-zA-Z]*|--[a-zA-Z-]+)\s+)*-[a-zA-Z]*c[a-zA-Z]*\s+(.*)$/s;

/**
 * Commands inside `$(...)` or backticks, which run in their own right.
 *
 * `echo $(rm -rf x)` deletes the file however harmless the outer command is,
 * and the substitution is invisible to a scan that reads the outer words only.
 * Each one is returned for rescanning rather than refused on sight, for the
 * same reason `sh -c` is: a substitution that only reads is still a read, and
 * `echo $(git rev-parse HEAD)` is an ordinary thing to run.
 *
 * Single quotes suppress substitution, so a `$(` inside them is literal and is
 * skipped — the token scan has already marked that span quoted.
 */
function substitutions(statement: string): string[] {
  const found: string[] = [];
  for (const match of statement.matchAll(/\$\(([^()]*)\)|`([^`]*)`/g)) {
    // A `$(` inside single quotes is literal. Checked against the scan rather
    // than by counting quotes, so the answer matches every other rule's.
    const literal = tokenize(statement).some(
      (t) => t.quoted && t.raw.startsWith("'") && t.raw.includes(match[0]),
    );
    const inner = (match[1] ?? match[2] ?? "").trim();
    if (inner && !literal) found.push(inner);
  }
  return found;
}

/**
 * The command `env -S "..."` was asked to run, or null when there is none.
 *
 * `-S` is the one wrapper flag whose value is a whole command rather than a
 * setting, so it can be neither skipped (the write goes unseen) nor read as
 * the command name (it is a whole string, not a word). Returned for rescanning
 * instead, the same treatment `sh -c` gets.
 */
function envDashS(tokens: Token[]): string | null {
  if (tokens[0]?.value.split("/").pop() !== "env") return null;
  for (let i = 1; i < tokens.length; i++) {
    const value = tokens[i].value;
    if (value === "-S" || value === "--split-string") return tokens[i + 1]?.value ?? null;
    if (value.startsWith("-S")) return value.slice(2);
    if (value.startsWith("--split-string=")) return value.slice("--split-string=".length);
  }
  return null;
}

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
  // Path-stripped the way `commandHead` strips it, so `/bin/sh -c` matches.
  const unwrapped = unwrap(statement);
  const [first, ...rest] = unwrapped.split(/\s+/).filter(Boolean);
  const pathless = first ? [first.split("/").pop(), ...rest].join(" ") : unwrapped;
  const match = SHELL_DASH_C.exec(pathless);
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

    // An interactive shell is a write channel this predicate cannot see into,
    // whether it is reached as a bare `sudo`, as `sudo -s`, or by naming the
    // shell outright. Two rounds of review each closed one spelling of this and
    // left the others — `sudo -s` refused while `bash`, `sudo bash` and `su`
    // walked through — so the rule is stated once here over the unwrapped
    // command rather than as a case per spelling.
    //
    // `head` empty means every word was a wrapper or its flags, which is the
    // `sudo -s` shape: no command left to run, so the wrapper is the command.
    const escape = head || lastWrapper(statement);
    if (INTERACTIVE_SHELLS.has(escape) && !shellPayload(statement)) {
      return `interactive shell escape: ${statement.trim()}`;
    }

    // Depth-limited so a pathological `sh -c "sh -c ..."` cannot spin. One
    // level is every real invocation; the limit is about termination, not about
    // a nesting an agent is expected to reach.
    const payload = depth < 4 ? shellPayload(statement) : null;
    const innerReason = payload ? blockedWriteCommand(payload, depth + 1) : null;
    if (innerReason) {
      return `write-capable command inside a shell wrapper: ${statement.trim()} (${innerReason})`;
    }

    // Against the unwrapped command's tokens, so `sudo sed -i` and `xargs rm`
    // are the writes they wrap, and a flag letter inside a quoted argument is
    // data rather than a flag.
    const command_ = commandTokens(statement);

    const flag = flagWrite(command_);
    if (flag) {
      return `\`${commandHead(statement)} ${flag}\` writes: ${statement.trim()}`;
    }

    const subcommand = gitWrite(command_);
    if (subcommand) {
      return `\`git ${subcommand}\` writes: ${statement.trim()}`;
    }

    // `eval` and `env -S` both take their argument as a command, so both are
    // the `sh -c` shape without the shell: rescanned rather than refused
    // outright, for the same reason — a payload that only reads is still a
    // read. `env -S` is why `-S` is absent from WRAPPER_VALUE_FLAGS.env.
    const nested = depth < 4
      ? (command_[0]?.value === "eval"
          ? tokenize(statement).slice(1).map((t) => t.value).join(" ")
          : envDashS(tokenize(statement)))
      : null;
    if (nested) {
      const reason = blockedWriteCommand(nested, depth + 1);
      if (reason) {
        return `write-capable command inside a wrapper: ${statement.trim()} (${reason})`;
      }
    }

    // A substitution runs whatever it contains, whatever the outer command is.
    if (depth < 4) {
      for (const inner of substitutions(statement)) {
        const reason = blockedWriteCommand(inner, depth + 1);
        if (reason) {
          return `write-capable command substitution: ${statement.trim()} (${reason})`;
        }
      }
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
