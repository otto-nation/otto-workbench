#!/usr/bin/env bash
# Shared UI helpers and path constants — sourced by all workbench scripts.
#
# This file is a facade that sources focused sub-modules for backward compatibility.
# All functions previously defined here are now in their own modules:
#   output.sh  — colors, info, warn, err, success, title, skip, sed_i
#   prompts.sh — confirm, confirm_n, confirm_step, prompt_overwrite, select_menu, select_subdirs, conf_get
#   files.sh   — install_symlink, install_file, copy_dir, symlink_dir, apply_config_patch
#   setup.sh   — require_command, install_cask, register_step, run_steps, run_migrations
#
# Sourcing patterns (use git rev-parse to find the repo root — depth-independent):
#   install.sh        . "$DOTFILES_DIR/lib/ui.sh"
#   */setup.sh        . "$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)/lib/ui.sh"
#   */steps.sh        . "$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)/lib/ui.sh"
#   bin/*             _SELF="$(readlink ...)"; . "$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)/lib/ui.sh"

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
  # shellcheck source=prompts.sh
  . "$_ui_lib_dir/prompts.sh"
  # shellcheck source=files.sh
  . "$_ui_lib_dir/files.sh"
  # shellcheck source=setup.sh
  . "$_ui_lib_dir/setup.sh"
  # shellcheck source=state.sh
  . "$_ui_lib_dir/state.sh"
  # shellcheck source=commands.sh
  . "$_ui_lib_dir/commands.sh"
fi

unset _ui_lib_dir
