---
name: Architecture — Current State
description: File layout, component map, and key patterns for otto-workbench (updated 2026-04-19)
type: project
---

Last verified: 2026-04-19. Significant restructuring in PR #36.

## Repo
/Users/isaacgarza/git/otto-nation/otto-workbench

## lib/ modules (post-refactor)
- `lib/constants.sh` — single source of truth for all paths; auto-derives WORKBENCH_DIR from BASH_SOURCE[0]
- `lib/ui.sh` — install helpers: `install_symlink`, `symlink_dir`, `install_file`, `copy_dir`, `select_menu`, `confirm`
- `lib/output.sh` — color output: `info`, `success`, `warn`, `err`
- `lib/files.sh` — file utilities
- `lib/conventions.sh` — shared convention helpers
- `lib/summary.sh` — summary display helpers
- `lib/components.sh` — component discovery helpers
- `lib/prompts.sh` — interactive prompt helpers
- `lib/migrations.sh` — migration framework: `run_component_migrations`, `run_all_migrations`
- `lib/registries.sh` — `collect_registries`, `iter_registry_env`, `registry_passes_install_check`
- `lib/worktree.sh` — worktree management helpers (added PR #36)
- `lib/ai/core.sh` — commit/PR conventions, constants
- `lib/ai/commit.sh`, `lib/ai/pr.sh`, `lib/ai/review.sh`, `lib/ai/prompts.sh`, `lib/ai/compact_diff.sh`

## Component Map
- `git/steps.sh` → `sync_git()`
- `zsh/steps.sh` → `sync_zsh()` [auto-discovers layers from zsh/config.d/*/]
- `bin/steps.sh` → `sync_bin()`
- `task/steps.sh` → `sync_task()`
- `mise/steps.sh` → `sync_mise()`
- `ai/steps.sh` → `sync_ai()` [wrapper]
- `ai/claude/steps.sh` → `sync_claude()`
- `docker/steps.sh` → `sync_docker()`
- `terminals/steps.sh` → `sync_terminals()`

## Script Layout (post-PR #36 reorganization)
- `bin/` — core workbench scripts: `otto-workbench`, `get-secret`, `mem-analyze`, `migrations`, `task`, `validate-*`, `template`
- `ai/bin/` — AI-specific scripts: `claude-rules`, `generate-tool-context`, `serena-mcp`
- `git/bin/` — git helpers: `generate-changelog`, `generate-git-rules`
- `docker/bin/` — docker helpers: `cleanup-testcontainers`
- `zsh/bin/` — zsh helpers: `aliases`
- `terminals/bin/` — terminal helpers: `ghostty-terminfo-push`

## Removed Components
- `ai/kiro/` — removed 2026-04-17 (migration: `ai/claude/migrations/20260417-remove-kiro.sh`)
- `bin/claude-init` — removed 2026-04-17 (migration: `bin/migrations/20260417-remove-claude-init.sh`)
- `terminals/iterm/` — removed (iTerm support dropped)

## New Additions (PR #36)
- `lib/worktree.sh` — worktree management (326 lines)
- `tests/worktree.bats` — worktree tests (202 lines)
- `bin/template` — canonical template for new bin scripts with -h/--help
- `ai/claude/skills/anatomy/` — anatomy skill (generate-anatomy.sh)
- `ai/claude/skills/dream/` — dream skill (already existed prior)
- `ai/bin/registry.yml`, `git/bin/registry.yml`, `docker/bin/registry.yml`, `terminals/bin/registry.yml`, `zsh/bin/registry.yml` — per-subdir registries

## Key Patterns
- All paths in scripts come from `constants.sh` — no inline `$HOME/` paths
- zsh layers auto-discovered: `for layer in "$ZSH_CONFIG_SRC_DIR"/*/`
- zsh `loader.zsh` is copied (not symlinked); `step_zsh_loader()` handles it separately
- `bin/` uses extglob `!(*.*)`  to symlink only extensionless executables
- AI setup auto-discovers tools: `for _dir in "$SCRIPT_DIR"/*/; do [[ -f "${_dir}steps.sh" ]]`
- All new scripts must implement `-h`/`--help` using the `bin/template` pattern
- `bin/template` is the canonical starting point for new scripts

## Intentional $HOME/ Exceptions
- `install.sh:55` — single-quoted grep pattern for PATH check (must be literal)
- `lib/ai/commit.sh` (formerly `lib/ai-commit.sh`) — task subprocess context, constants.sh never loaded there
- `zsh/steps.sh` — escaped `\$HOME` in heredoc (runtime expansion in .zshrc)
