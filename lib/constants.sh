#!/usr/bin/env bash
# Shared path and filename constants — sourced automatically via lib/ui.sh.
#
# HOME-relative paths work on any machine without any caller setup.
# Workbench source paths are derived from this file's own location so callers
# never need to set WORKBENCH_DIR, DOTFILES_DIR, SCRIPT_DIR, or _AI_DIR.
# Any caller may set WORKBENCH_DIR before sourcing to override the derived path.

# shellcheck disable=SC2034  # All constants are used by sourcing scripts

# ─── Workbench root ───────────────────────────────────────────────────────────
# Auto-derived from this file's location (lib/constants.sh → workbench root).
# Respects DOTFILES_DIR (set by install.sh) and WORKBENCH_DIR if already set.
if [[ -z "${WORKBENCH_DIR:-}" ]]; then
  # Resolve symlinks so WORKBENCH_DIR is correct even if lib/constants.sh is symlinked.
  _constants_src="${BASH_SOURCE[0]}"
  while [[ -L "$_constants_src" ]]; do
    _constants_link="$(readlink "$_constants_src")"
    if [[ "$_constants_link" == /* ]]; then
      _constants_src="$_constants_link"
    else
      _constants_src="$(cd "$(dirname "$_constants_src")" && pwd)/$_constants_link"
    fi
  done
  unset _constants_link
  WORKBENCH_DIR="${DOTFILES_DIR:-"$(cd "$(dirname "$_constants_src")/.." && pwd)"}"
  unset _constants_src
fi

# ─── Shell dotfiles ───────────────────────────────────────────────────────────
ZSHRC_FILE="$HOME/.zshrc"
BASHRC_FILE="$HOME/.bashrc"
ENV_LOCAL_FILE="$HOME/.env.local"
GITCONFIG_FILE="$HOME/.gitconfig"
GIT_HOOKS_DIR="$HOME/.git-hooks"

# ─── XDG-style config and local dirs ─────────────────────────────────────────
LOCAL_BIN_DIR="$HOME/.local/bin"
ZSH_CONFIG_DIR="$HOME/.config/zsh/config.d"
STARSHIP_CONFIG_FILE="$HOME/.config/starship.toml"
TASK_CONFIG_DIR="$HOME/.config/task"
TASKFILE_ENV="$TASK_CONFIG_DIR/taskfile.env"

# ─── Workbench runtime state ──────────────────────────────────────────────────
# Written by setup scripts; read by zsh snippets and sync steps.
# Never committed — machine-specific, lives in ~/.config/workbench/.
WORKBENCH_STATE_DIR="$HOME/.config/workbench"

# ─── Docker / Colima ──────────────────────────────────────────────────────────
DOCKER_RUN_DIR="$HOME/.docker/run"
COLIMA_DIR="$HOME/.colima"
TESTCONTAINERS_FILE="$HOME/.testcontainers.properties"
# Symlink written by docker/setup.sh pointing to docker/<runtime>/aliases.zsh.
# Sourced by zsh/config.d/aliases/docker.zsh to load runtime-specific config.
DOCKER_RUNTIME_ALIASES="$WORKBENCH_STATE_DIR/docker-aliases.zsh"
MIGRATIONS_STATE_FILE="$WORKBENCH_STATE_DIR/migrations.applied"
INSTALLED_STATE_FILE="$WORKBENCH_STATE_DIR/installed.components"

# ─── Claude Code ──────────────────────────────────────────────────────────────
CLAUDE_DIR="$HOME/.claude"
CLAUDE_CONFIG_FILE="$HOME/.claude.json"
CLAUDE_GUIDELINES_FILE="$CLAUDE_DIR/CLAUDE.md"
CLAUDE_SETTINGS_FILE="$CLAUDE_DIR/settings.json"
CLAUDE_RULES_DIR="$HOME/.claude/rules"
CLAUDE_AGENTS_DIR="$HOME/.claude/agents"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"

# ─── Workbench source — root ──────────────────────────────────────────────────
BIN_SRC_DIR="$WORKBENCH_DIR/bin"
LIB_SRC_DIR="$WORKBENCH_DIR/lib"
TASKFILE_SRC="$WORKBENCH_DIR/Taskfile.global.yml"

# ─── Workbench source — docker ────────────────────────────────────────────────
DOCKER_SRC_DIR="$WORKBENCH_DIR/docker"
TESTCONTAINERS_SRC="$WORKBENCH_DIR/docker/testcontainers.properties"

# ─── Workbench source — terminals ────────────────────────────────────────────
TERMINALS_SRC_DIR="$WORKBENCH_DIR/terminals"
GHOSTTY_SRC_DIR="$WORKBENCH_DIR/terminals/ghostty"
GHOSTTY_CONFIG_TEMPLATE="$WORKBENCH_DIR/terminals/ghostty/config.template"
GHOSTTY_CONFIG_DIR="$HOME/.config/ghostty"
GHOSTTY_CONFIG_FILE="$HOME/.config/ghostty/config"

# ─── Workbench source — git ───────────────────────────────────────────────────
GIT_SRC_DIR="$WORKBENCH_DIR/git"
GIT_SHARED_CONFIG="$WORKBENCH_DIR/git/gitconfig.shared"
GIT_CONFIG_TEMPLATE="$WORKBENCH_DIR/git/gitconfig.template"
GIT_HOOKS_SRC_DIR="$WORKBENCH_DIR/git/hooks"
GIT_IDENTITY_DIR="$HOME/.config/git/identities"

# ─── Workbench source — zsh ───────────────────────────────────────────────────
ZSH_SRC_DIR="$WORKBENCH_DIR/zsh"
ZSH_CONFIG_SRC_DIR="$WORKBENCH_DIR/zsh/config.d"
ZSH_ZSHRC_TEMPLATE="$WORKBENCH_DIR/zsh/.zshrc"
ENV_LOCAL_TEMPLATE="$WORKBENCH_DIR/zsh/.env.local.template"
STARSHIP_SRC_FILE="$WORKBENCH_DIR/zsh/starship.toml"
ZSH_LOADER_SRC="$ZSH_CONFIG_SRC_DIR/loader.zsh"
ZSH_LOADER_DST="$ZSH_CONFIG_DIR/loader.zsh"
ZSH_SNIPPET_GLOB="*.zsh"

# ─── Workbench source — AI ────────────────────────────────────────────────────
AI_SRC_DIR="$WORKBENCH_DIR/ai"
GUIDELINES_RULES_SRC_DIR="$WORKBENCH_DIR/ai/guidelines/rules"
RULES_GLOB="*.md"

CLAUDE_SRC_DIR="$WORKBENCH_DIR/ai/claude"
SERENA_SRC_DIR="$WORKBENCH_DIR/ai/serena"
CLAUDE_MCPS_SRC_DIR="$WORKBENCH_DIR/ai/claude/mcps"
CLAUDE_GUIDELINES_SRC="$WORKBENCH_DIR/ai/claude/CLAUDE.md"
CLAUDE_SETTINGS_SRC="$WORKBENCH_DIR/ai/claude/settings.json"
CLAUDE_SYNC_SETTINGS_JQ="$WORKBENCH_DIR/ai/claude/sync-settings.jq"
CLAUDE_SKILLS_SRC_DIR="$WORKBENCH_DIR/ai/claude/skills"
CLAUDE_AGENTS_SRC_DIR="$WORKBENCH_DIR/ai/claude/agents"
CLAUDE_TEMPLATES_DIR="$WORKBENCH_DIR/ai/claude/templates"

# ─── User overrides (gitignored, local customizations) ──────────────────────
USER_AI_DIR="$WORKBENCH_DIR/user/ai"
USER_CLAUDE_DIR="$USER_AI_DIR/claude"
USER_AGENTS_DIR="$USER_CLAUDE_DIR/agents"
USER_SKILLS_DIR="$USER_CLAUDE_DIR/skills"
USER_RULES_DIR="$USER_AI_DIR/guidelines/rules"
USER_GUIDELINES_SRC="$USER_CLAUDE_DIR/CLAUDE.md"
USER_GUIDELINES_LOCAL="$USER_CLAUDE_DIR/CLAUDE.local.md"
USER_SETTINGS_SRC="$USER_CLAUDE_DIR/settings.json"

# ─── Workbench source — editors ───────────────────────────────────────────────
ZED_SETTINGS_SRC="$WORKBENCH_DIR/editors/zed/settings.json"
ZED_SYNC_SETTINGS_JQ="$WORKBENCH_DIR/editors/zed/sync-settings.jq"
SUBLIME_SETTINGS_SRC="$WORKBENCH_DIR/editors/sublime/Preferences.sublime-settings"
SUBLIME_SYNC_SETTINGS_JQ="$WORKBENCH_DIR/editors/sublime/sync-settings.jq"

# ─── Editors — runtime paths ──────────────────────────────────────────────────
ZED_CONFIG_DIR="$HOME/.config/zed"
ZED_SETTINGS_FILE="$HOME/.config/zed/settings.json"
SUBLIME_PREFS_DIR="$HOME/Library/Application Support/Sublime Text/Packages/User"
SUBLIME_SETTINGS_FILE="$HOME/Library/Application Support/Sublime Text/Packages/User/Preferences.sublime-settings"

# ─── Generated rule files ─────────────────────────────────────────────────────
TOOLS_GENERATED_RELPATH="ai/guidelines/rules/tools.generated.md"
TOOLS_WORKFLOW_GENERATED_RELPATH="ai/guidelines/rules/tools-workflow.generated.md"
GIT_GENERATED_RELPATH="ai/guidelines/rules/git.generated.md"
TOOLS_GENERATED_FILE="$WORKBENCH_DIR/$TOOLS_GENERATED_RELPATH"
TOOLS_WORKFLOW_GENERATED_FILE="$WORKBENCH_DIR/$TOOLS_WORKFLOW_GENERATED_RELPATH"
GIT_GENERATED_FILE="$WORKBENCH_DIR/$GIT_GENERATED_RELPATH"
