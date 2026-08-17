# Bash Tool — Permission Patterns

Patterns that trigger unsuppressible permission prompts in Claude Code's static analyzer. These apply to Bash tool usage, not to writing shell scripts.

`ai/claude/bin/claude-bash-guard`, the PreToolUse hook for the Bash tool, enforces the patterns below that can be matched mechanically — not every section has a guard rule. Each block message cites the section holding its alternatives (`See bash-tool.md § <Section>`), so a new guard rule needs a section here to cite; `tests/claude_settings.bats` fails if it has none.

## Avoid Command Substitution in Arguments

- Never use `$(...)` command substitution inside Bash tool commands — Claude Code's static analyzer cannot resolve the substitution, triggering a "cannot be statically analyzed" permission prompt. Run the inner command first, then use the result in the next call:
  - `which pr` then `head -80 /path/from/previous/result` instead of `head -80 "$(which pr)"`
  - `git rev-parse --show-toplevel` then `ls /path/from/previous/result` instead of `ls "$(git rev-parse --show-toplevel)"`

## Avoid Compound `cd` Commands

- Never use `cd <path> && <command>` — compound commands containing `cd` trigger an unsuppressible security prompt in Claude Code. This applies wherever a statement begins, not just at the start of the command: `mkdir -p x; cd x && ls` counts. A newline is a statement separator too, so a `cd` on its own line followed by more lines is still compound — and when a later line writes a file, the prompt is the stricter "contains cd with write operation". Use these alternatives instead:
  - `git -C <path> ...` for git commands
  - `gh --repo <owner/repo> ...` or `gh api repos/<owner>/<repo>/...` for GitHub CLI (no directory needed for API calls)
  - Run the command directly with absolute paths when possible — `pytest /abs/path/tests/foo_test.py`, `bats /abs/path/tests/foo.bats` (both derive their root from the file paths, so they need no cwd change)
  - A bare `cd <dir>` on its own — no `&&`, no `;`, nothing after it — as its own call. The Bash tool keeps that working directory for later calls. This is the only reliable way to run a whole suite (`bats tests/`, `bin/local/validate-all`) that resolves paths from the repo root

## Avoid `env -C`

- Never run a command through `env -C <dir> ...` (or `env --chdir=<dir> ...`) — the analyzer reports "env with -C flag cannot be statically analyzed", which no permission rule can suppress. `env -C` is not an escape hatch from the compound-`cd` rule, it is a second unsuppressible prompt. Run a bare `cd <dir>` as its own call first, per § Avoid Compound `cd` Commands

## Avoid Shell Function Definitions

- Never define a shell function in a Bash tool command — both `name() { ...; }` and `function name { ...; }` make the parser classify the whole command as too complex to analyze ("Contains function_definition"), which skips the allow list entirely so no permission rule can match it. Write the command directly with absolute paths:
  - `grep -rn "pattern" /abs/path/tests/ | head -40` instead of `cd() { :; }; W=/abs/path; grep -rn "pattern" "$W/tests/" | head -40`
- A no-op stub such as `cd() { :; }` is not a way around the compound-`cd` rule — it trades a rule you can satisfy for a prompt you cannot suppress. Use an absolute path, `git -C <path>`, or `gh --repo` instead

## Avoid Wrapping Commands in `sh -c`

- Never wrap a command in `sh -c "..."` (or `bash -c`, `zsh -c`) — the analyzer cannot see inside the quoted string, so it asks approval for the wrapper as a whole and no allow-list entry ever matches the real command. Worse, the offered "don't ask again" rule keys on that exact string, so the next `sh -c` prompts again
- `sh -c "cd /dir; cmd"` is the usual reason to reach for it. Run a bare `cd /dir` as its own call, then `cmd` as the next one — per § Avoid Compound `cd` Commands
- If you need a specific shell's behavior (`sh` word-splitting, glob handling), put the script in a file with the Write tool and run `sh /tmp/probe.sh` — the file is also readable, which a quoted one-liner is not

## Avoid Absolute Paths to PATH Binaries

- Never invoke a binary by its absolute path when the same binary is on `PATH` — the permission allow list keys on the bare command name (`Bash(cat:*)`, `Bash(mise:*)`), so an absolute form never matches it and prompts every time. This applies wherever a statement begins, not just at the start of the command: `ls; /bin/cat file` counts. Call the bare name instead — it resolves through `PATH` to the same binary:
  - `cat /path/to/file` instead of `/bin/cat /path/to/file`
  - `env` instead of `/usr/bin/env`
  - `mise doctor` instead of `~/.local/bin/mise doctor`
  - `gh pr view` instead of `/opt/homebrew/bin/gh pr view`
  - `node --test x.mjs` instead of `~/.local/share/mise/installs/node/24.18.1/bin/node --test x.mjs`
- The directories covered are `/bin`, `/usr/bin`, `/usr/local/bin`, `/opt/homebrew/bin`, `/opt/homebrew/sbin`, any `.local/bin`, and a version manager's shim and install dirs (`.local/share/mise/shims`, `.local/share/mise/installs/*/bin`, `.asdf/shims`, `.asdf/installs/*/bin`) — the workbench installs its own scripts into `~/.local/bin`, so its tools are reached by bare name too
- `which <tool>` printing a shell function body (mise and other `activate`-style tools define one) is not a reason to switch to the absolute path — the bare name still resolves through `PATH` in the Bash tool
- Resolving a version manager's shim to the versioned binary behind it is not a reason either. The shim is on `PATH` and picks the same version the project pins; the resolved path only adds a prompt and goes stale on the next upgrade
- This is the opposite of the `bin/local/` rule: workbench *repo* scripts must use the *relative* path, installed binaries must use the *bare* name. Both exist so a single allow-list entry covers every invocation
- Like the other statement-anchored checks below, the hook enforcing this scans every line of the command with quoted spans stripped — a later line counts, but such a path inside a quoted argument or a heredoc body is left alone

## Avoid Env-Var Prefix Syntax

- Never prefix a command with `VAR=value command` — Claude Code's permission matcher sees `VAR=value` as the command name, triggering a prompt every time. This applies wherever a statement begins, not just at the start of the command: `true; W=/tmp x` counts. Use tool-native alternatives:
  - `task --global REPO_DIR=/path ...` (go-task variable syntax, not `REPO_DIR=/path task ...`)
  - `mise -C /path run ...` (not `REPO_DIR=/path mise run ...`)
  - `otto-workbench --workbench-dir /path ...` (not `WORKBENCH_DIR=/path otto-workbench ...`)

## Avoid Brace Expansion

- Never use `{a,b,c}` brace expansion in Bash commands — Claude Code flags it with an unsuppressible "Brace expansion" permission prompt. Use these alternatives instead:
  - List files as separate arguments: `wc -l file1.go file2.go file3.go`
  - Use a glob when files share a pattern: `grep -rE "pattern" activities/*.go`
  - Use `find ... | xargs` for more complex selections

## Avoid Shell Variable Expansion

- Never use `echo "$VAR"` or `$VAR` in Bash tool commands — Claude Code's static analyzer flags shell variable references as "simple_expansion", triggering a permission prompt. Use `printenv VAR` instead, which reads the variable without shell expansion:
  - If the value is already in CLAUDE.md or conversation context, don't run a command at all
- A `for f in ...; do ... "$f" ...; done` loop over a list you already know is the most common way this fires. `Bash(for:*)` is allowed — it is the `$f` that prompts. Name the values directly instead:
  - `tail -n +1 file1.sql file2.sql file3.sql` to dump several files, each under its own `==> file <==` header — this is the direct replacement for a `for` loop wrapping `cat`/`rtk read` plus an `echo` banner
  - `grep -n "pattern" file1 file2 file3` and most other tools take a file list, so the loop is not needed at all
  - Write out each statement in full when the tool genuinely takes one target at a time
- Single quotes prevent expansion, so `grep -n '$HOME' file` and `perl -pe 's/$//' file` are fine — the hook enforcing this strips single-quoted spans but not double-quoted ones, since `"$f"` does expand
- Like the other statement-anchored checks, the hook skips heredoc bodies, so a `$VAR` inside one being written to a file is left alone — every other line is scanned

## Avoid Shell Redirects That Write Files

- Never write file content with `echo ... > file` or `printf ... >> file` — Claude Code gates a Bash redirect on the write path and asks per directory ("always allow access to `schema/` from this project"), while the Edit and Write tools are allow-listed outright. Unlike the other sections here, the prompt is a file-access gate, not a static-analysis failure — so the fix is a different tool, not a different command:
  - Write tool for a new file, Edit tool to append to or modify an existing one — both show a diff, which a redirect does not
  - `cat > file <<EOF` and `tee file` are the same anti-pattern; use the Write tool instead
  - `>` silently truncates, so a mistyped path destroys a file with no diff to review — another reason the tools are the safer default
- Redirecting to a scratch path (`/dev/null`, `/tmp/...`) is fine and is not blocked — that is where a redirect belongs
- Capturing another command's *output* to a file (`some-cmd > report.json`) is fine too; the tools have no equivalent. Only `echo` and `printf`, which emit literal content you already have, are blocked
- A path in a repo the session isn't working in prompts regardless of the tool. Add it with `--add-dir` or `permissions.additionalDirectories` in that project's settings rather than approving each subdirectory

## Avoid Nesting Quotes of the Same Type

- Never nest a quoted string inside another quote of the same type — the analyzer re-pairs the quotes differently than you intended and reports "Parser skipped input between top-level statements", which no permission rule can suppress. The quote counts stay even, so this is not something the guard hook can detect; it is on you to avoid the shape
- The usual trigger is building a payload by hand: `printf '%s' '{"command":"perl -e 's/a/b/' f"}' | prog` — the inner `'s/a/b/'` closes the outer span early and the rest of the line is parsed as loose tokens
- Write the payload to a file with the Write tool and redirect it in instead — `prog < /tmp/payload.json`. This also survives review, since the file is readable on its own
- For a one-off argument that only needs one level of nesting, use the other quote type for the inner string (`--jq '.state'` inside a double-quoted command, or the reverse)

## Avoid `find -exec`

- Never use `find ... -exec` — Claude Code blocks `-exec` even with `Bash(find:*)` allowed because `-exec` can run arbitrary commands. Use piped alternatives instead:
  - `find ... -print0 | xargs -0 grep ...` instead of `find ... -exec grep ... {} \;`
  - `find ... -print0 | xargs -0 <command>` for other commands
  - Both `find` and `xargs` are already auto-allowed
