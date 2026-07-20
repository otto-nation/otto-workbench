# Bash Tool — Permission Patterns

Patterns that trigger unsuppressible permission prompts in Claude Code's static analyzer. These apply to Bash tool usage, not to writing shell scripts.

## Avoid Command Substitution in Arguments

- Never use `$(...)` command substitution inside Bash tool commands — Claude Code's static analyzer cannot resolve the substitution, triggering a "cannot be statically analyzed" permission prompt. Run the commands sequentially instead:
  - `which pr` then `head -80 /path/from/previous/result` instead of `head -80 "$(which pr)"`
  - `git rev-parse --show-toplevel` then `ls /path/from/previous/result` instead of `ls "$(git rev-parse --show-toplevel)"`

## Avoid Compound `cd` Commands

- Never use `cd <path> && <command>` — compound commands starting with `cd` trigger an unsuppressible security prompt in Claude Code. Use these alternatives instead:
  - `git -C <path> ...` for git commands
  - `gh --repo <owner/repo> ...` or `gh api repos/<owner>/<repo>/...` for GitHub CLI (no directory needed for API calls)
  - Run the command directly with absolute paths when possible

## Avoid Env-Var Prefix Syntax

- Never prefix a command with `VAR=value command` — Claude Code's permission matcher sees `VAR=value` as the command name, triggering a prompt every time. Use tool-native alternatives:
  - `task --global REPO_DIR=/path ...` (go-task variable syntax, not `REPO_DIR=/path task ...`)
  - `mise -C /path run ...` (not `REPO_DIR=/path mise run ...`)
  - `otto-workbench --workbench-dir /path ...` (not `WORKBENCH_DIR=/path otto-workbench ...`)

## Avoid Brace Expansion

- Never use `{a,b,c}` brace expansion in Bash commands — Claude Code flags it with an unsuppressible "Brace expansion" permission prompt. Use these alternatives instead:
  - List files as separate arguments: `wc -l file1.go file2.go file3.go`
  - Use a glob when files share a pattern: `grep -rE "pattern" activities/*.go`
  - Use `find ... | xargs` for more complex selections

## Avoid Shell Variable Expansion

- Never use `echo "$VAR"` or `$VAR` in Bash tool commands — Claude Code's static analyzer flags shell variable references as "simple_expansion", triggering a permission prompt. Use `printenv VAR` instead, which reads the variable without shell expansion. If the value is already in CLAUDE.md or conversation context, don't run a command at all

## Avoid `find -exec`

- Never use `find ... -exec` — Claude Code blocks `-exec` even with `Bash(find:*)` allowed because `-exec` can run arbitrary commands. Use piped alternatives instead:
  - `find ... -print0 | xargs -0 grep ...` instead of `find ... -exec grep ... {} \;`
  - `find ... -print0 | xargs -0 <command>` for other commands
  - Both `find` and `xargs` are already auto-allowed
