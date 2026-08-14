# Bash Tool — Permission Patterns

Patterns that trigger unsuppressible permission prompts in Claude Code's static analyzer. These apply to Bash tool usage, not to writing shell scripts.

`ai/claude/bin/claude-bash-guard`, the PreToolUse hook for the Bash tool, enforces the patterns below that can be matched mechanically — not every section has a guard rule. The lead bullet of each section, and the message the guard blocks with, are both generated from `ai/claude/bin/bash-guard.rules.yml`: add the rule there and run `bin/local/generate-bash-guard`. Bullets outside the `<!-- rule -->` markers are hand-written; edits inside them are overwritten.

## Avoid Command Substitution in Arguments

<!-- rule:cmd_substitution -->
- Never use `$(...)` command substitution inside Bash tool commands — Claude Code's static analyzer cannot resolve the substitution, triggering a "cannot be statically analyzed" permission prompt. Run the inner command first, then use the result in the next call:
<!-- /rule -->
  - `which pr` then `head -80 /path/from/previous/result` instead of `head -80 "$(which pr)"`
  - `git rev-parse --show-toplevel` then `ls /path/from/previous/result` instead of `ls "$(git rev-parse --show-toplevel)"`

## Avoid Compound `cd` Commands

<!-- rule:compound_cd -->
- Never use `cd <path> && <command>` — compound commands containing `cd` trigger an unsuppressible security prompt in Claude Code. This applies wherever a statement begins, not just at the start of the command: `mkdir -p x; cd x && ls` counts. Use these alternatives instead:
<!-- /rule -->
  - `git -C <path> ...` for git commands
  - `gh --repo <owner/repo> ...` or `gh api repos/<owner>/<repo>/...` for GitHub CLI (no directory needed for API calls)
  - Run the command directly with absolute paths when possible — `pytest /abs/path/tests/foo_test.py`, `bats /abs/path/tests/foo.bats` (both derive their root from the file paths, so they need no cwd change)
  - A bare `cd <dir>` on its own — no `&&`, no `;`, nothing after it — as its own call. The Bash tool keeps that working directory for later calls. This is the only reliable way to run a whole suite (`bats tests/`, `bin/local/validate-all`) that resolves paths from the repo root

## Avoid `env -C`

<!-- rule:env_chdir -->
- Never run a command through `env -C <dir> ...` (or `env --chdir=<dir> ...`) — the analyzer reports "env with -C flag cannot be statically analyzed", which no permission rule can suppress; `env -C` is not an escape hatch from the compound-`cd` rule, it is a second unsuppressible prompt. Use a bare `cd <dir>` first, per § Avoid Compound `cd` Commands
<!-- /rule -->

## Avoid Shell Function Definitions

<!-- rule:func_def -->
- Never define a shell function in a Bash tool command — both `name() { ...; }` and `function name { ...; }` make the parser classify the whole command as too complex to analyze ("Contains function_definition"), which skips the allow list entirely so no permission rule can match it. Write the command directly with absolute paths:
<!-- /rule -->
  - `grep -rn "pattern" /abs/path/tests/ | head -40` instead of `cd() { :; }; W=/abs/path; grep -rn "pattern" "$W/tests/" | head -40`
- A no-op stub such as `cd() { :; }` is not a way around the compound-`cd` rule — it trades a rule you can satisfy for a prompt you cannot suppress. Use an absolute path, `git -C <path>`, or `gh --repo` instead

## Avoid Absolute Paths to System Binaries

<!-- rule:sysbin -->
- Never invoke a system binary by its absolute path (`/bin/cat`, `/usr/bin/grep`, `/usr/bin/env`) — the permission allow list keys on the bare command name (`Bash(cat:*)`), so the `/bin` and `/usr/bin` forms never match it and prompt every time. This applies wherever a statement begins, not just at the start of the command: `ls; /bin/cat file` counts. Call the bare name instead — it resolves through `PATH` to the same binary:
<!-- /rule -->
  - `cat /path/to/file` instead of `/bin/cat /path/to/file`
  - `env` instead of `/usr/bin/env`
- This is the opposite of the `bin/local/` rule: workbench scripts must use the *relative* path, system binaries must use the *bare* name. Both exist so a single allow-list entry covers every invocation
- Like the other statement-anchored checks below, the hook enforcing this scans the quote-stripped first line — a `/bin/...` path inside a quoted argument or a heredoc body is left alone, and a `/bin/...` call on a later line is not caught

## Avoid Env-Var Prefix Syntax

<!-- rule:env_var_prefix -->
- Never prefix a command with `VAR=value command` — Claude Code's permission matcher sees `VAR=value` as the command name, triggering a prompt every time. This applies wherever a statement begins, not just at the start of the command: `true; W=/tmp x` counts. Use tool-native alternatives:
<!-- /rule -->
  - `task --global REPO_DIR=/path ...` (go-task variable syntax, not `REPO_DIR=/path task ...`)
  - `mise -C /path run ...` (not `REPO_DIR=/path mise run ...`)
  - `otto-workbench --workbench-dir /path ...` (not `WORKBENCH_DIR=/path otto-workbench ...`)

## Avoid Brace Expansion

<!-- rule:brace_expansion -->
- Never use `{a,b,c}` brace expansion in Bash commands — Claude Code flags it with an unsuppressible "Brace expansion" permission prompt. Use these alternatives instead:
<!-- /rule -->
  - List files as separate arguments: `wc -l file1.go file2.go file3.go`
  - Use a glob when files share a pattern: `grep -rE "pattern" activities/*.go`
  - Use `find ... | xargs` for more complex selections

## Avoid Shell Variable Expansion

<!-- rule:var_expansion -->
- Never use `echo "$VAR"` or `$VAR` in Bash tool commands — Claude Code's static analyzer flags shell variable references as "simple_expansion", triggering a permission prompt. Use `printenv VAR` instead, which reads the variable without shell expansion:
<!-- /rule -->
  - If the value is already in CLAUDE.md or conversation context, don't run a command at all

## Avoid `find -exec`

<!-- rule:find_exec -->
- Never use `find ... -exec` — Claude Code blocks `-exec` even with `Bash(find:*)` allowed because `-exec` can run arbitrary commands. Use piped alternatives instead:
<!-- /rule -->
  - `find ... -print0 | xargs -0 grep ...` instead of `find ... -exec grep ... {} \;`
  - `find ... -print0 | xargs -0 <command>` for other commands
  - Both `find` and `xargs` are already auto-allowed
