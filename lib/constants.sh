#!/bin/bash
# Shared path and filename constants — sourced automatically via lib/ui.sh.
#
# All paths are derived from $HOME so they work on any machine.
# Add a constant here when a path or filename is referenced in more than one file.
# Never set DOTFILES_DIR or SCRIPT_DIR here — those are set by each script.

# shellcheck disable=SC2034  # All constants are used by sourcing scripts

# ─── Shell dotfiles ───────────────────────────────────────────────────────────
ZSHRC_FILE="$HOME/.zshrc"
BASHRC_FILE="$HOME/.bashrc"
ENV_LOCAL_FILE="$HOME/.env.local"
GITCONFIG_FILE="$HOME/.gitconfig"
GITCONFIG_LOCAL_FILE="$HOME/.gitconfig.local"
GIT_HOOKS_DIR="$HOME/.git-hooks"

# ─── XDG-style config and local dirs ─────────────────────────────────────────
LOCAL_BIN_DIR="$HOME/.local/bin"
ZSH_CONFIG_DIR="$HOME/.config/zsh/config.d"
STARSHIP_CONFIG_FILE="$HOME/.config/starship.toml"
TASK_CONFIG_DIR="$HOME/.config/task"

# ─── Claude Code ──────────────────────────────────────────────────────────────
CLAUDE_DIR="$HOME/.claude"
CLAUDE_CONFIG_FILE="$HOME/.claude.json"
CLAUDE_RULES_DIR="$HOME/.claude/rules"
CLAUDE_AGENTS_DIR="$HOME/.claude/agents"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"

# ─── Kiro ─────────────────────────────────────────────────────────────────────
KIRO_AGENTS_DIR="$HOME/.kiro/agents"
KIRO_STEERING_DIR="$HOME/.kiro/steering"

# ─── Generated rule files (repo-relative — prefix with $REPO_ROOT for full paths) ───
TOOLS_GENERATED_RELPATH="ai/guidelines/rules/tools.generated.md"
GIT_GENERATED_RELPATH="ai/guidelines/rules/git.generated.md"
