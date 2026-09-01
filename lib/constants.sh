#!/usr/bin/env bash
# Path and filename constants, auto-derived from the workbench root.
#
# The single source of truth for `WORKBENCH_DIR`, `LOCAL_BIN_DIR`,
# `ZSH_CONFIG_DIR`, `CLAUDE_DIR`, `MIGRATIONS_STATE_FILE`, `INSTALL_YML_FILE`,
# `INSTALLED_STATE_FILE`, `MAINTENANCE_LAST_FILE`, and every other shared path.
#
# HOME-relative paths work on any machine without any caller setup. Workbench
# source paths are derived from this file's own location, so callers never need
# to set `WORKBENCH_DIR`, `DOTFILES_DIR`, `SCRIPT_DIR`, or `_AI_DIR`; any caller
# may set `WORKBENCH_DIR` before sourcing to override the derived path.
#
# No public functions — constants only. Sources `roots.sh`.

# shellcheck disable=SC2034  # All constants are used by sourcing scripts

# ─── Workbench root ───────────────────────────────────────────────────────────
# Auto-derived from this file's location (lib/constants.sh → workbench root).
# Respects DOTFILES_DIR (set by install.sh) and WORKBENCH_DIR if already set.
if [[ -z "${WORKBENCH_DIR:-}" ]]; then
  _constants_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  WORKBENCH_DIR="${DOTFILES_DIR:-"$(dirname "$_constants_dir")"}"
  unset _constants_dir
fi

# _main_worktree DIR — prints DIR's main worktree, or nothing when DIR is not a
# bare repo. Ends on a command that succeeds either way: a bare `[[ ]] &&` here
# would return 1 from the source and abort any caller running under `set -e`,
# which maintenance/bin/otto-workbench-maintenance does.
_main_worktree() {
  local dir="$1"
  [[ "$(git -C "$dir" config --get core.bare 2>/dev/null)" == "true" ]] || return 0
  git -C "$dir" worktree list 2>/dev/null | awk '/\[main\]/{print $1; exit}'
}

# Stable symlink target — in bare repos, resolves to the main worktree so
# symlinks created by install_symlink survive worktree switches.
# In normal repos (non-bare), equals WORKBENCH_DIR.
if [[ -z "${WORKBENCH_STABLE_DIR:-}" ]]; then
  _main_wt="$(_main_worktree "$WORKBENCH_DIR")"
  WORKBENCH_STABLE_DIR="${_main_wt:-$WORKBENCH_DIR}"
  unset _main_wt
fi
unset -f _main_worktree

# ─── Shell dotfiles ───────────────────────────────────────────────────────────
ZSHRC_FILE="$HOME/.zshrc"
BASHRC_FILE="$HOME/.bashrc"
ENV_LOCAL_FILE="$HOME/.env.local"
GITCONFIG_FILE="$HOME/.gitconfig"
GIT_HOOKS_DIR="$HOME/.git-hooks"
SSH_DIR="$HOME/.ssh"
SSH_CONFIG_FILE="$SSH_DIR/config"
SSH_KNOWN_HOSTS_FILE="$SSH_DIR/known_hosts"

# ─── XDG-style config and local dirs ─────────────────────────────────────────
LOCAL_BIN_DIR="$HOME/.local/bin"
ZSH_CONFIG_DIR="$HOME/.config/zsh/config.d"
STARSHIP_CONFIG_FILE="$HOME/.config/starship.toml"
TASK_CONFIG_DIR="$HOME/.config/task"
TASKFILE_ENV="$TASK_CONFIG_DIR/taskfile.env"

# ─── Workbench roots (config / state / cache) ─────────────────────────────────
# WORKBENCH_CONFIG_DIR, WORKBENCH_STATE_DIR, WORKBENCH_CACHE_DIR are owned by
# lib/roots.sh — its own module because the otto-ai-tools tarball ships it
# without shipping this file.
# shellcheck source=./roots.sh
. "$(dirname "${BASH_SOURCE[0]}")/roots.sh"

# The single root everything used to share, before the split into three.
# Only adopt_legacy_workbench_root in lib/migrations.sh reads this — it is the
# path being emptied, not a path anything should still write to.
LEGACY_WORKBENCH_ROOT="$HOME/.config/workbench"

# Core components — always synced, never tracked in install.yml.
CORE_COMPONENTS="bin git zsh task"

# ─── Docker / Colima ──────────────────────────────────────────────────────────
DOCKER_RUN_DIR="$HOME/.docker/run"
COLIMA_DIR="$HOME/.colima"
TESTCONTAINERS_FILE="$HOME/.testcontainers.properties"
# Symlink written by docker/setup.sh pointing to docker/<runtime>/aliases.zsh.
# Sourced by zsh/config.d/aliases/docker.zsh to load runtime-specific config.
DOCKER_RUNTIME_ALIASES="$WORKBENCH_STATE_DIR/docker-aliases.zsh"
MIGRATIONS_STATE_FILE="$WORKBENCH_STATE_DIR/migrations.applied"
# Written by maintenance/bin/otto-workbench-maintenance, read by `otto-workbench
# maintenance status`.
MAINTENANCE_LAST_FILE="$WORKBENCH_STATE_DIR/maintenance.last"
INSTALLED_STATE_FILE="$WORKBENCH_STATE_DIR/installed.components"
# State despite the name reading like config: lib/state.sh writes it through
# state_record and state_set, and it is what installed.components migrated
# into. The hand-authored settings are overrides/ under the config root.
INSTALL_YML_FILE="$WORKBENCH_STATE_DIR/install.yml"

# ─── Project registry ─────────────────────────────────────────────────────────
# The repos on this machine that use otto-workbench, one absolute path per line.
# lib/projects.sh owns every read and write; ai/lib/workbench_paths.py spells the
# same filename for Python, and tests/workbench_roots.bats fails when the two
# drift. Newline-delimited text rather than YAML for the same reason
# migrations.applied is: every write is an append and every read is a scan, and
# a YAML file would pay a `yq` fork on each of them.
PROJECTS_REGISTRY_NAME="projects.registry"
PROJECTS_REGISTRY_FILE="$WORKBENCH_STATE_DIR/$PROJECTS_REGISTRY_NAME"

# ─── Workbench config ─────────────────────────────────────────────────────────
# Hand-authored settings, one file per scope: the global file under the config
# root and one per repo at its toplevel. lib/config.sh reads them;
# ai/lib/workbench_config.py is the typed owner and the source
# config.schema.json is generated from. Every name below is spelled a second
# time there, and tests/config.bats fails when the two sets drift.
WORKBENCH_CONFIG_NAME="config.yml"
WORKBENCH_CONFIG_FILE="$WORKBENCH_CONFIG_DIR/$WORKBENCH_CONFIG_NAME"
WORKBENCH_PROJECT_CONFIG_NAME=".workbench.yml"

# The generated JSON Schema, and the raw URL that serves it. Pinned to main
# rather than a release tag: the config on a machine tracks whatever workbench
# is installed, and main is where the schema is regenerated.
WORKBENCH_CONFIG_SCHEMA_NAME="config.schema.json"
WORKBENCH_REPO_RAW_URL="https://raw.githubusercontent.com/otto-nation/otto-workbench/main"
WORKBENCH_CONFIG_SCHEMA_URL="$WORKBENCH_REPO_RAW_URL/$WORKBENCH_CONFIG_SCHEMA_NAME"

# The modeline a config file is born with, so an editor's YAML language server
# validates the file against that schema as the user hand-edits it.
WORKBENCH_CONFIG_HEADER="# yaml-language-server: \$schema=$WORKBENCH_CONFIG_SCHEMA_URL"

# The dotted config keys bash reads. Spelled here for the same reason the file
# names above are: workbench_config.py holds the other spelling, and
# tests/config.bats fails when a pair drifts.
GITHUB_SSH_443_CONFIG_KEY="github.ssh_over_443"
ISSUE_PROVIDER_CONFIG_KEY="issue_tracker.provider"

# The name `otto-workbench config get` prints for the machine-wide scope. A
# report over other people's repos distinguishes an answer a repo gave from one
# it inherited, and this is the string that tells them apart — so it is a wire
# value between the two languages, cross-validated like the keys above.
WORKBENCH_GLOBAL_SCOPE="global"

# ─── Review state ─────────────────────────────────────────────────────────────
# The shell half of two joins the Python side also spells out — this file for
# bash, ai/lib/workbench_paths.py's reviews_dir() and retro-scan's
# CONSUMED_REVIEWS_NAME for Python. tests/workbench_roots.bats cross-validates
# both pairs, the same way it does the roots they hang off.
REVIEWS_DIR="$WORKBENCH_STATE_DIR/reviews"
# Written by retro-scan, then read and emptied by the retro skill's
# retro-complete.sh — the list of review directories a retro has consumed.
RETRO_CONSUMED_REVIEWS_FILE="$WORKBENCH_STATE_DIR/retro-consumed-reviews.txt"

# ─── Claude Code ──────────────────────────────────────────────────────────────
CLAUDE_DIR="$HOME/.claude"
CLAUDE_CONFIG_FILE="$HOME/.claude.json"
CLAUDE_GUIDELINES_FILE="$CLAUDE_DIR/CLAUDE.md"
CLAUDE_SETTINGS_FILE="$CLAUDE_DIR/settings.json"
# What the last sync wrote into the file above, so the next one can tell its own
# entries from a user's. It lives beside the settings file's *state*, not inside
# it: Claude Code validates settings.json and rejects the whole file when a hook
# entry appears under any key other than `hooks` — which is what a manifest
# recording managed hooks inline looked like.
CLAUDE_SETTINGS_MANIFEST="$WORKBENCH_STATE_DIR/claude-settings.manifest.json"
CLAUDE_RULES_DIR="$HOME/.claude/rules"
CLAUDE_AGENTS_DIR="$HOME/.claude/agents"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
# Claude Code's own installer, which step_install_claude pipes to bash and the
# cask migration replays.
CLAUDE_INSTALL_URL="https://claude.ai/install.sh"
# The launcher that installer manages, a symlink into ~/.local/share/claude.
# The migration tests this path by name rather than asking `command -v claude`,
# which a Homebrew cask still on PATH answers just as readily.
CLAUDE_NATIVE_BIN="$HOME/.local/bin/claude"

# ─── Agent Skills — cross-harness install root ───────────────────────────────
# Pi's built-in cross-harness discovery root, and the shared standard's own
# location. Pi stats each entry before recursing (dist/core/skills.js:171-176),
# so a symlinked skill directory here is followed like a real one — which is why
# no per-harness copy is needed. Named for the standard rather than for Pi: the
# skills installed here belong to no one harness, and CLAUDE_SKILLS_DIR above is
# its Claude Code counterpart.
AGENTS_SKILLS_DIR="$HOME/.agents/skills"

# ─── Pi coding agent ──────────────────────────────────────────────────────────
# Pi reads global settings from ~/.pi/agent/settings.json. PI_LEGACY_SETTINGS_FILE
# is the path an earlier sync wrote to, which Pi never read.
PI_HOME="$HOME/.pi"
PI_AGENT_DIR="$PI_HOME/agent"
PI_SETTINGS_FILE="$PI_AGENT_DIR/settings.json"
PI_LEGACY_SETTINGS_FILE="$PI_HOME/settings.json"
PI_SKILLS_DIR="$PI_AGENT_DIR/skills"
# Pi's own installer, which step_install_pi pipes to bash. It installs the npm
# package into a managed root under ~/.pi and links the launcher into the first
# writable PATH directory it recognises — ~/.local/bin here, which the workbench
# already puts on PATH.
PI_INSTALL_URL="https://pi.dev/install.sh"

# ─── Workbench source — root ──────────────────────────────────────────────────
BIN_SRC_DIR="$WORKBENCH_DIR/bin"
BIN_REGISTRY_FILE="$WORKBENCH_DIR/bin/registry.yml"
LIB_SRC_DIR="$WORKBENCH_DIR/lib"
TASKFILE_SRC="$WORKBENCH_DIR/Taskfile.global.yml"
INSTALL_COMPONENTS_FILE="$WORKBENCH_DIR/install.components"

# ─── Workbench source — brew ─────────────────────────────────────────────────
BREW_SRC_DIR="$WORKBENCH_DIR/brew"
BREWFILE="$WORKBENCH_DIR/brew/Brewfile"

# ─── Workbench source — maintenance ──────────────────────────────────────────
MAINTENANCE_SRC_DIR="$WORKBENCH_DIR/maintenance"

# ─── Workbench source — docker ────────────────────────────────────────────────
DOCKER_SRC_DIR="$WORKBENCH_DIR/docker"
TESTCONTAINERS_SRC="$WORKBENCH_DIR/docker/testcontainers.properties"

# ─── Workbench source — terminals ────────────────────────────────────────────
TERMINALS_SRC_DIR="$WORKBENCH_DIR/terminals"
GHOSTTY_SRC_DIR="$WORKBENCH_DIR/terminals/ghostty"
GHOSTTY_CONFIG_TEMPLATE="$WORKBENCH_DIR/terminals/ghostty/config.template"
GHOSTTY_CONFIG_DIR="$HOME/.config/ghostty"
GHOSTTY_CONFIG_FILE="$HOME/.config/ghostty/config"

# ─── Worktrunk ───────────────────────────────────────────────────────────────
WORKTRUNK_CONFIG_FILE="$HOME/.config/worktrunk/config.toml"

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
ENV_LOCAL_TEMPLATE="${ENV_LOCAL_TEMPLATE:-$WORKBENCH_DIR/zsh/.env.local.template}"
STARSHIP_SRC_FILE="$WORKBENCH_DIR/zsh/starship.toml"
ZSH_LOADER_SRC="$ZSH_CONFIG_SRC_DIR/loader.zsh"
ZSH_LOADER_DST="$ZSH_CONFIG_DIR/loader.zsh"
ZSH_SNIPPET_GLOB="*.zsh"

# ─── Workbench source — AI ────────────────────────────────────────────────────
AI_SRC_DIR="$WORKBENCH_DIR/ai"
# Canonical skills, installed into both harnesses' discovery roots by
# ai/skills/steps.sh. Harness-neutral: the Agent Skills standard is the format
# and neither Claude Code nor Pi needs a translated copy.
SKILLS_SRC_DIR="$WORKBENCH_DIR/ai/skills"
GUIDELINES_RULES_SRC_DIR="$WORKBENCH_DIR/ai/guidelines/rules"
RULES_GLOB="*.md"

AI_MEMORY_BACKUP_DIR="$WORKBENCH_DIR/ai/memory"
CLAUDE_SRC_DIR="$WORKBENCH_DIR/ai/claude"
PI_SRC_DIR="$WORKBENCH_DIR/ai/pi"
PI_SETTINGS_SRC="$PI_SRC_DIR/settings.json"
PI_SYNC_SETTINGS_JQ="$PI_SRC_DIR/sync-settings.jq"
PI_SKILLS_SRC_DIR="$WORKBENCH_DIR/ai/claude/pi/skills"
SERENA_SRC_DIR="$WORKBENCH_DIR/ai/serena"
CLAUDE_MCPS_SRC_DIR="$WORKBENCH_DIR/ai/claude/mcps"
CLAUDE_GUIDELINES_SRC="$WORKBENCH_DIR/ai/claude/CLAUDE.md"
CLAUDE_SETTINGS_SRC="$WORKBENCH_DIR/ai/claude/settings.json"
CLAUDE_SYNC_SETTINGS_JQ="$WORKBENCH_DIR/ai/claude/sync-settings.jq"
CLAUDE_AGENTS_SRC_DIR="$WORKBENCH_DIR/ai/claude/agents"
CLAUDE_TEMPLATES_DIR="$WORKBENCH_DIR/ai/claude/templates"

# ─── User overrides (hand-authored, so they live under the config root) ──────
USER_AI_DIR="$WORKBENCH_CONFIG_DIR/overrides/ai"
# Harness-neutral, matching ai/skills — the layer overrides a tree neither
# harness owns.
USER_SKILLS_DIR="$USER_AI_DIR/skills"
USER_CLAUDE_DIR="$USER_AI_DIR/claude"
USER_AGENTS_DIR="$USER_CLAUDE_DIR/agents"
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

# ─── Public surface snapshots ─────────────────────────────────────────────────
# One snapshot per released package, written by bin/local/generate-public-surface
# and diffed against a base ref by bin/local/check-surface-compat. Placement is
# load-bearing: the root release-please config sets exclude-paths: ["ai/claude"],
# so each snapshot's diff is attributed to the package that owns it.
WORKBENCH_SURFACE_RELPATH="public-surface.json"
AI_TOOLS_SURFACE_RELPATH="ai/claude/public-surface.json"
# Every snapshot, in the order the gate reports them.
PUBLIC_SURFACE_RELPATHS=("$WORKBENCH_SURFACE_RELPATH" "$AI_TOOLS_SURFACE_RELPATH")

# ─── Generated rule files ─────────────────────────────────────────────────────
TOOLS_GENERATED_RELPATH="ai/guidelines/rules/tools.generated.md"
GIT_GENERATED_RELPATH="ai/guidelines/rules/git.generated.md"
TOOLS_GENERATED_FILE="$WORKBENCH_DIR/$TOOLS_GENERATED_RELPATH"
GIT_GENERATED_FILE="$WORKBENCH_DIR/$GIT_GENERATED_RELPATH"
