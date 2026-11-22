---
harness: [claude]
---

# Bash Tool — Permission Patterns

Patterns that trigger unsuppressible permission prompts in Claude Code's static analyzer, plus one silent-failure pattern (§ Subagents Reset the Working Directory). They apply to Bash tool usage, not to writing shell scripts, and to Claude Code only.

`ai/claude/bin/claude-bash-guard` (PreToolUse hook) blocks the mechanically matchable ones; each block message names the fix and cites its section here (`See bash-tool.md § <Section>`). `tests/claude_settings_writes.bats` fails if a guard rule has no section.

## Avoid Command Substitution in Arguments

- No `$(...)` in Bash tool commands. Run the inner command first, then use its result in the next call

## Avoid Compound `cd` Commands

- No `cd <path> && <command>`, wherever a statement begins (a newline is a separator too). Use `git -C <path>`, `gh <subcommand> --repo <owner/repo>` (`--repo` after the subcommand), `gh api repos/...`, absolute paths (`bats /abs/path/tests/foo.bats`), or a bare `cd <dir>` as its own call in a main session

## Subagents Reset the Working Directory

- The rule (paths absolute or `-C`-qualified, brief the subagent, confirm where it ran) is `git-operations.md` § Cross-Worktree Safety; this section adds only the Claude Code forms.
- A bare `cd <dir>` does not persist between Bash calls in a subagent — every call starts again in the directory the subagent was spawned in, which is the *parent session's* cwd, not any directory the subagent chose. Unlike the sections above, this is not a permission prompt: nothing is blocked, nothing warns, and the command runs somewhere other than where it was meant to
- For a suite or script that must resolve paths from the repo root (`bats tests/`, `bin/local/validate-all`), write a wrapper to `/tmp` with the Write tool whose first line is `cd /abs/path`, and run `bash /tmp/<name>.sh` — same technique as § Avoid Wrapping Commands in `sh -c`. A wrapper *file* is not the `sh -c "..."` anti-pattern — the script is readable, and its `cd` is inside a file rather than in the analyzed command string
- Invoke the wrapper with `bash`, never `sh` — only `Bash(bash:*)` is allow-listed, so `sh /tmp/<name>.sh` prompts, and the "don't ask again" rule it offers is an exact match on that one filename. The next wrapper gets a new name and prompts again, which is how a checkout accumulates dozens of single-use `Bash(sh /tmp/...)` grants that can never match twice
- Ending the turn ends the subagent: do not start background work there (`run_in_background` included). Run it in the foreground bounded by the Bash tool's own `timeout` and finish it before ending the turn. `claude-bash-guard` enforces this when the PreToolUse payload carries a non-empty `agent_id`. (`general.md` § Waiting on Background Work) states this for every harness

## Avoid `env -C`

- No `env -C <dir>` / `env --chdir=<dir>`; run a bare `cd <dir>` as its own call first

## Avoid Shell Function Definitions

- No `name() { ...; }` or `function name { ...; }` (a no-op `cd() { :; }` stub included); write the command directly with absolute paths

## Avoid Wrapping Commands in `sh -c`

- No `sh -c`, `bash -c`, `zsh -c`. Run a bare `cd /dir` as its own call, then the command. For shell-specific behaviour, write a file with the Write tool and run `bash /tmp/probe.sh` (`sh` only when POSIX behaviour is the thing under test)

## Avoid Absolute Paths to PATH Binaries

- Call a binary on `PATH` by its bare name (`cat`, `gh`, `mise`, `node`), not `/bin/cat`, `~/.local/bin/x`, or a version manager's shim or install path; the allow list keys on the bare name. `which` printing a function body is no reason to use the absolute path
- Workbench *repo* scripts take the relative path (next section); installed binaries take the bare name

## Invoke Repo Scripts by Relative Path

- Invoke workbench repo scripts by the path relative to the repo root, with no `./` and never absolute (`bin/local/validate-skills`); the grant is `Bash(bin/*)`. Granted directories: `bin/`, `git/bin/`, `ai/bin/`, `ai/claude/bin/` (see CONTRIBUTING.md § Permission grants)
- **A subagent is the exception:** a relative path silently runs the parent worktree's copy, so use the absolute path there and accept the prompt (§ Subagents Reset the Working Directory)

## Avoid Env-Var Prefix Syntax

- No `VAR=value command`. Use the tool's own flag: `git -C`, `pr create --repo-dir`, `mise -C`, `otto-workbench --workbench-dir`

## Avoid Brace Expansion

- No `{a,b,c}`. List the arguments separately or use a glob

## Avoid Shell Variable Expansion

- No `$VAR` or `echo "$VAR"`. Read an env var with `printenv VAR`; for a `for f in ...` loop over known values, name them directly (`tail -n +1 f1 f2 f3`). Single-quoted `'$VAR'` and heredoc bodies are fine

## Avoid Shell Redirects That Write Files

- No `echo ... > file`, `printf ... >> file`, `cat > file <<EOF`, or `tee file` to write content; use the Write tool (new file) or Edit tool (change/append). Redirecting to `/dev/null` or `/tmp`, or capturing a command's output (`cmd > report.json`), is fine
- `>` silently truncates, so a mistyped path destroys a file with no diff; the Write and Edit tools show one
- A path in a repo outside the session prompts whatever the tool; add it with `--add-dir` or `permissions.additionalDirectories`

## Avoid Backgrounding with `&`

- No standalone `&` and no `nohup`; use `run_in_background: true` and make any follow-up request in a separate call. `&&`, `2>&1`, `&>`, `|&` are fine, as is a short `sleep` settling a just-started server
- Give a full-suite run an explicit `timeout` (up to 600000 ms); a killed `bats tests/` leaves orphans. `pkill` prompting is correct, so add no allow-list entry for it

## Avoid Nesting Quotes of the Same Type

- Never nest a quoted string inside another quote of the same type — the analyzer re-pairs the quotes differently than you intended and reports "Parser skipped input between top-level statements", which no permission rule can suppress. The quote counts stay even, so this is not something the guard hook can detect; it is on you to avoid the shape
- The usual trigger is building a payload by hand: `printf '%s' '{"command":"perl -e 's/a/b/' f"}' | prog` — the inner `'s/a/b/'` closes the outer span early and the rest of the line is parsed as loose tokens
- Write the payload to a file with the Write tool and redirect it in instead — `prog < /tmp/payload.json`. This also survives review, since the file is readable on its own
- For a one-off argument that only needs one level of nesting, use the other quote type for the inner string (`--jq '.state'` inside a double-quoted command, or the reverse)

## Avoid `find -exec`

- No `find ... -exec`; use `find ... -print0 | xargs -0 <command>`
