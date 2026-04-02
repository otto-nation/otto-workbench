# Claude Code

## Tools
- Code exploration: use Serena (prefer symbolic reads over full file reads)
- Multi-step decisions: use sequential-thinking
- Third-party library docs: use context7

## Rules
Coding guidelines load automatically from `~/.claude/rules/` based on file type.
- Run `claude-rules add <domain> "rule"` to append a machine-specific rule
- Run `claude-rules project add "rule"` to append a rule to the current repo's CLAUDE.md
