#!/usr/bin/env bash
# Shared UI helpers and path constants — sourced by all workbench scripts.
#
# This file is a facade that sources focused sub-modules for backward compatibility.
# All functions previously defined here are now in their own modules — the list
# is the sourcing block below, and docs/libraries.md documents each module's
# functions from the doc comments in the module itself.
#
# Sourcing patterns (all use WORKBENCH_DIR via git rev-parse):
#   install.sh        . "$DOTFILES_DIR/lib/ui.sh"
#   */setup.sh        WORKBENCH_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"; . "$WORKBENCH_DIR/lib/ui.sh"
#   */steps.sh        WORKBENCH_DIR="$(git rev-parse --show-toplevel)"; . "$WORKBENCH_DIR/lib/ui.sh"
#   bin/*             WORKBENCH_DIR="$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)"; . "$WORKBENCH_DIR/lib/ui.sh"
#
# None of the git forms above survive an exported GIT_DIR: with one set, git
# skips discovery, so `git -C <subdir> rev-parse --show-toplevel` answers
# <subdir> and the bare form answers the caller's cwd. A script a git hook runs
# — pre-push reaches bin/local/validate-all and bin/local/check-surface-compat —
# must derive its root from its own path instead:
#   bin/local/*       WORKBENCH_DIR="$(cd "$(dirname "$_SELF")/../.." && pwd)"

# Source path and filename constants — resolved relative to this file in bash
if [[ -n "${BASH_VERSION:-}" ]]; then
  # shellcheck source=./constants.sh
  . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/constants.sh"
fi

# Resolve lib directory for sourcing sub-modules
_ui_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# Output helpers work in both bash and zsh
# shellcheck source=output.sh
. "$_ui_lib_dir/output.sh"

# Prompt, file, and setup helpers are bash-only
if [[ -n "${BASH_VERSION:-}" ]]; then
  # shellcheck source=portable.sh
  . "$_ui_lib_dir/portable.sh"
  # shellcheck source=prompts.sh
  . "$_ui_lib_dir/prompts.sh"
  # shellcheck source=files.sh
  . "$_ui_lib_dir/files.sh"
  # shellcheck source=setup.sh
  . "$_ui_lib_dir/setup.sh"
  # shellcheck source=state.sh
  . "$_ui_lib_dir/state.sh"
  # shellcheck source=projects.sh
  . "$_ui_lib_dir/projects.sh"
  # shellcheck source=config.sh
  . "$_ui_lib_dir/config.sh"
  # shellcheck source=commands.sh
  . "$_ui_lib_dir/commands.sh"
fi

unset _ui_lib_dir
