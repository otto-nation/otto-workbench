#!/usr/bin/env bash
# The three user-level roots the workbench writes to, each resolved through the
# same chain:
#
# ```
# WORKBENCH_<ROOT>_DIR  →  XDG_<ROOT>_HOME/workbench  →  built-in default
# ```
#
# <!-- include: bin/local/generate-doc-reference --roots-table -->
#
# `install.yml` sits under state despite the name: `lib/state.sh` owns every
# write to it, and it is what the old `installed.components` file migrated into.
# It records what a sync found or installed, not anything a user chose to type.
#
# Machines set up before the split keep everything in `~/.config/workbench`.
# `adopt_legacy_workbench_root` in [`lib/migrations.sh`](../lib/migrations.sh)
# carries that directory across on the next sync, entry by entry, and runs ahead
# of the migration framework because `migrations.applied` is one of the files it
# moves. Nothing falls back to the old path once it has run — the adoption is the
# entire compatibility story.
#
# Routing is by name and spelled out in three branches, not two plus a
# fallthrough: `_LEGACY_CONFIG_ENTRIES` goes to the config root,
# `_LEGACY_UNCLAIMED_ENTRIES` is skipped and left in the legacy root, and
# everything else goes to the state root. That last default is deliberate — the
# inventory behind the split found state files no manifest written in advance had
# listed — but it must not also absorb a name no root holds any more: adoption
# runs before any migration reads its bookkeeping, so an entry a completed
# migration deleted on purpose (`logs/`, for one) would come back with nothing
# left to take it out again. Adding one to the unclaimed list is the fix whenever
# a migration prunes a top-level name from a root.
#
# A file the new root already holds is normally kept on both sides and warned
# about rather than clobbered. The exception is the append-only ledgers —
# `trail.jsonl` and `usage/*.jsonl` — which are concatenated instead: their only
# writers open them in append mode, and `otto-log` sorts every record by `ts`
# after loading, so one history split across two files reassembles either way.
# The rule is keyed on those names, not on the `.jsonl` extension, because the
# review artifacts (`session.jsonl`, `post.jsonl`, `*.holistic.jsonl`) are
# whole-file writes whose convention is prior-content-first.
#
# Sourcing this file twice in one process is routine — `ui.sh` reaches it through
# `constants.sh`, and `registries.sh` loads it on its own — so each root is
# resolved afresh every time rather than the first source winning forever. A root
# a caller named stays put across those re-sources; a root this file derived
# re-derives, which is what lets a caller change `HOME` and get the roots that go
# with it. Without that, a test sandboxing `HOME` wrote its settings file to the
# sandbox and its manifest to the machine's real state root, because `CLAUDE_DIR`
# re-derives from `HOME` and `WORKBENCH_STATE_DIR` did not.
#
# Its own module rather than part of `constants.sh` because two other consumers
# need the roots without the rest: the `otto-ai-tools` tarball ships `roots.sh`
# alongside its own `ui.sh` facade (see `BASH_MODULES` in
# `ai/claude/bin/build-otto-ai-tools-tarball`), and `registries.sh` sources it
# directly when a caller has not loaded `constants.sh`.
#
# Two definitions outside `lib/` express the same chain, and
# `tests/workbench_roots.bats` cross-validates all three:
#
# - [`ai/lib/workbench_paths.py`](../ai/lib/workbench_paths.py) — the Python
#   owner. Exposes `config_dir()`, `state_dir()`, `cache_dir(consumer=None)`,
#   `trail_dir()`, and `reviews_dir()`, resolved per call rather than frozen at
#   import. `cache_dir` takes a consumer name and rejects anything but a bare
#   directory name — a path would land outside the tree the root's owner globs
#   over. `trail_dir()` takes nothing: every trail writer shares one root,
#   `<state>/trail/`, with one file per month. `reviews_dir()` is the sole owner
#   of the reviews join, so the review system and the tool that reads its output
#   — `retro-scan` for the findings — cannot disagree about where a review is.
# - [`zsh/config.d/aliases/docker.zsh`](../zsh/config.d/aliases/docker.zsh) —
#   spelled inline, because `WORKBENCH_DIR` is unknown at shell startup and
#   sourcing would add a file read to every shell.
#
# #### Trails
#
# Every AI script appends to `trail_dir()`, in a file named for the emitting
# event's UTC month. The layout mirrors `ai_usage.ledger_dir`: rotation falls out
# of the filename, `--since` drops whole files without opening them, and nothing
# needs a pruning job. `_emit` takes an `fcntl.flock` on the open handle inside
# the module's thread lock — one file now takes appends from concurrent
# processes (`pr` and the script it spawned), and a short write (NFS, a signal,
# an rlimit boundary) can split a record across two `write()` calls, letting the
# other process's append land in the gap.
#
# The `20260814-unify-trail-root` migration carried the pre-cutover review trails
# into `trail/legacy.jsonl`. `otto-log` always reads a file whose stem does not
# name a month, which is what keeps it visible under `--since`.

# _wb_root OVERRIDE PREVIOUS XDG_HOME FALLBACK — resolve one root. OVERRIDE is
# what the root currently holds, PREVIOUS what this file last resolved it to.
#
# An override that is exported but empty counts as unset, matching how the XDG
# spec reads its own variables — a bare `export WORKBENCH_STATE_DIR=` in a shell
# profile falls through to the default rather than resolving every root to the
# filesystem root.
#
# An override equal to PREVIOUS is this file's own output rather than a caller's
# choice, and re-derives — the re-source behaviour the header block describes.
# Comparing against the last resolution is what separates the two, since by the
# time a second source runs there is otherwise nothing to tell a caller's value
# from the one this file wrote there.
_wb_root() {
  local override="$1" previous="$2" xdg_home="$3" fallback="$4"
  if [[ -n "$previous" && "$override" == "$previous" ]]; then override=""; fi
  if [[ -n "$override" ]]; then
    printf '%s' "$override"
  elif [[ -n "$xdg_home" ]]; then
    printf '%s/workbench' "$xdg_home"
  else
    printf '%s' "$fallback"
  fi
}

# _wb_mark NAME RESOLVED OVERRIDE PREVIOUS — record RESOLVED under NAME as this
# file's own output, or empty NAME when OVERRIDE was a caller's choice.
#
# Emptying is what keeps a caller's root pinned across a re-source: the next one
# compares against an empty record, so the value it finds can never be mistaken
# for ours. Recording every resolution instead would retire the override after
# one source, since a caller's value and our own would then look the same.
_wb_mark() {
  local name="$1" resolved="$2" override="$3" previous="$4"
  if [[ -n "$override" && "$override" != "$previous" ]]; then
    declare -g "$name="
  else
    declare -g "$name=$resolved"
  fi
}

# shellcheck disable=SC2034  # All three roots are used by sourcing scripts

# What each root already holds, captured before the resolver overwrites it —
# _wb_mark needs to know whether a caller named the value or this file did.
_wb_had_config="${WORKBENCH_CONFIG_DIR:-}"
_wb_had_state="${WORKBENCH_STATE_DIR:-}"
_wb_had_cache="${WORKBENCH_CACHE_DIR:-}"

# Hand-authored settings: config.yml, overrides/.
WORKBENCH_CONFIG_DIR="$(_wb_root "$_wb_had_config" "${_WB_DERIVED_CONFIG_DIR:-}" "${XDG_CONFIG_HOME:-}" "$HOME/.config/workbench")"
_wb_mark _WB_DERIVED_CONFIG_DIR "$WORKBENCH_CONFIG_DIR" "$_wb_had_config" "${_WB_DERIVED_CONFIG_DIR:-}"

# Generated, machine-local data: reviews/, trail/, usage/, install.yml, migrations.applied.
# Written by setup scripts; read by zsh snippets and sync steps. Never committed.
#
# The move off the old ~/.config/workbench default is a hard cut — nothing falls
# back to the legacy path. What carries the data is the one-time adoption in
# lib/migrations.sh, which runs before any migration reads its own bookkeeping.
WORKBENCH_STATE_DIR="$(_wb_root "$_wb_had_state" "${_WB_DERIVED_STATE_DIR:-}" "${XDG_STATE_HOME:-}" "$HOME/.local/state/workbench")"
_wb_mark _WB_DERIVED_STATE_DIR "$WORKBENCH_STATE_DIR" "$_wb_had_state" "${_WB_DERIVED_STATE_DIR:-}"

# Recomputable data, safe to delete at any time: vertex-quota/.
WORKBENCH_CACHE_DIR="$(_wb_root "$_wb_had_cache" "${_WB_DERIVED_CACHE_DIR:-}" "${XDG_CACHE_HOME:-}" "$HOME/.cache/workbench")"
_wb_mark _WB_DERIVED_CACHE_DIR "$WORKBENCH_CACHE_DIR" "$_wb_had_cache" "${_WB_DERIVED_CACHE_DIR:-}"

# The resolver has done its work. This file is sourced into every script that
# loads lib/ui.sh, so leaving the helpers defined would leak them into all of
# them. The three _WB_DERIVED_* variables outlive the helpers by design — they
# are the record the next source in this process reads.
unset -f _wb_root _wb_mark
unset _wb_had_config _wb_had_state _wb_had_cache
