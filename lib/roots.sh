#!/usr/bin/env bash
# The three workbench roots — config, state, and cache.
#
# Each resolves through the same chain:
#   WORKBENCH_<ROOT>_DIR  →  XDG_<ROOT>_HOME/workbench  →  built-in default
#
# Its own module rather than part of constants.sh because the otto-ai-tools
# tarball ships this file directly (see BASH_MODULES in
# ai/claude/bin/build-otto-ai-tools-tarball). The tarball's ui.sh facade
# replaces constants.sh, so anything spelled there would have to be spelled
# again in the facade.
#
# Two other definitions express the same chain and must stay in step:
# ai/lib/workbench_paths.py for Python, and zsh/config.d/aliases/docker.zsh,
# which cannot source this file at shell startup. tests/workbench_roots.bats
# cross-validates all three.

# _wb_root OVERRIDE XDG_HOME FALLBACK — resolve one root.
# An override that is exported but empty counts as unset, matching how the XDG
# spec reads its own variables — a bare `export WORKBENCH_STATE_DIR=` in a shell
# profile falls through to the default rather than resolving every root to the
# filesystem root.
_wb_root() {
  local override="$1" xdg_home="$2" fallback="$3"
  if [[ -n "$override" ]]; then
    printf '%s' "$override"
  elif [[ -n "$xdg_home" ]]; then
    printf '%s/workbench' "$xdg_home"
  else
    printf '%s' "$fallback"
  fi
}

# shellcheck disable=SC2034  # All three roots are used by sourcing scripts

# Hand-authored settings: config.yml, overrides/, mcp-tools.json.
WORKBENCH_CONFIG_DIR="$(_wb_root "${WORKBENCH_CONFIG_DIR:-}" "${XDG_CONFIG_HOME:-}" "$HOME/.config/workbench")"

# Generated, machine-local data: reviews/, trail/, usage/, applied migrations.
# Written by setup scripts; read by zsh snippets and sync steps. Never committed.
#
# The move off the old ~/.config/workbench default is a hard cut — nothing falls
# back to the legacy path. What carries the data is the one-time adoption in
# lib/migrations.sh, which runs before any migration reads its own bookkeeping.
WORKBENCH_STATE_DIR="$(_wb_root "${WORKBENCH_STATE_DIR:-}" "${XDG_STATE_HOME:-}" "$HOME/.local/state/workbench")"

# Recomputable data, safe to delete at any time: vertex-quota/.
WORKBENCH_CACHE_DIR="$(_wb_root "${WORKBENCH_CACHE_DIR:-}" "${XDG_CACHE_HOME:-}" "$HOME/.cache/workbench")"

# The resolver has done its work. This file is sourced into every script that
# loads lib/ui.sh, so leaving the helper defined would leak it into all of them.
unset -f _wb_root
