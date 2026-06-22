# Bash Tool — Permission Patterns

Patterns that trigger unsuppressible permission prompts in Claude Code's static analyzer. These apply to Bash tool usage, not to writing shell scripts.

## Avoid Command Substitution in Arguments

- Never use `$(...)` command substitution inside Bash tool commands — Claude Code's static analyzer cannot resolve the substitution, triggering a "cannot be statically analyzed" permission prompt. Run the commands sequentially instead:
  - `which pr` then `head -80 /path/from/previous/result` instead of `head -80 "$(which pr)"`
  - `git rev-parse --show-toplevel` then `ls /path/from/previous/result` instead of `ls "$(git rev-parse --show-toplevel)"`
