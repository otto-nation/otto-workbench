#!/usr/bin/env bash
# Migration framework — discovers and runs per-component migrations with state tracking.
#
# Migration files live in <component>/migrations/YYYYMMDD-slug.sh and define a single
# idempotent function named migration_YYYYMMDD_slug (dashes replaced with underscores).
#
# State is tracked in $MIGRATIONS_STATE_FILE (one line per applied migration).
# Stale entries (pointing to removed migration files) are pruned automatically.
#
# Usage (from scripts that already source lib/ui.sh):
#   . "$WORKBENCH_DIR/lib/migrations.sh"
#   run_all_migrations              # discover and run across all components
#   run_component_migrations DIR    # run for a single component directory

# Guard: constants must be loaded (provides WORKBENCH_DIR, MIGRATIONS_STATE_FILE,
# LEGACY_WORKBENCH_ROOT, and the roots the adoption below moves data between)
if [[ -z "${WORKBENCH_DIR:-}" || -z "${LEGACY_WORKBENCH_ROOT:-}" ]]; then
  echo "ERROR: lib/migrations.sh requires WORKBENCH_DIR and LEGACY_WORKBENCH_ROOT (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

# shellcheck source=components.sh
. "$LIB_SRC_DIR/components.sh"

_array_contains() {
  local needle="$1"; shift
  local item
  for item in "$@"; do
    if [[ "$item" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

# _source_migration FILE — load a migration's definitions without letting the
# file take the sync down.
#
# A migration is sourced, so whatever it does at file scope runs in this shell.
# Two shapes there would abort run_all_migrations outright instead of reaching
# the warn-and-retry path below, which is the only place a failing migration is
# supposed to land:
#
#   - a top-level statement that returns non-zero, which under the `set -e` most
#     migration files carry exits the sync mid-component — a file that invokes
#     its own function is the way that happens in practice (#731).
#     validate-migrations rejects that shape now, but the framework must hold
#     even for a file the validator never saw
#   - the `set -e` itself, which outlives the source and would otherwise arm
#     errexit for every component that syncs after this one
#
# `.` runs as the left side of an `||`, a context where errexit is ignored for
# everything the sourced file executes, and the caller's own errexit setting is
# put back afterwards. The source's own status is returned so a file that fails
# to load is reported rather than silently treated as loaded.
_source_migration() {
  local migration="$1" status=0 errexit_on=false
  [[ $- != *e* ]] || errexit_on=true

  # shellcheck source=/dev/null
  . "$migration" || status=$?

  if [[ "$errexit_on" == true ]]; then
    set -e
  else
    set +e
  fi
  return "$status"
}

# run_component_migrations DIR
# Discovers DIR/migrations/*.sh, skips already-applied migrations, sources and runs
# each function, and records success. Failed migrations are not recorded and retry
# on the next run. Migrations must be idempotent.
run_component_migrations() {
  local dir="$1"
  local migrations_dir="$dir/migrations"
  [[ -d "$migrations_dir" ]] || return 0

  local state_file="$MIGRATIONS_STATE_FILE"
  mkdir -p "$(dirname "$state_file")"
  touch "$state_file"

  # Derive component-relative path for state tracking (e.g. "git", "terminals/ghostty")
  local component_rel="${dir#"$WORKBENCH_DIR/"}"

  local migration basename_m state_key fn_name applied=0 skipped=0
  for migration in "$migrations_dir"/*.sh; do
    [[ -f "$migration" ]] || continue
    basename_m="$(basename "$migration")"
    state_key="$component_rel/$basename_m"

    # Already applied — skip
    if grep -qxF "$state_key" "$state_file"; then
      skipped=$(( skipped + 1 ))
      continue
    fi

    # Derive function name: strip .sh, replace dashes with underscores
    fn_name="migration_${basename_m%.sh}"
    fn_name="${fn_name//-/_}"

    if ! _source_migration "$migration"; then
      warn "Migration $basename_m: could not be loaded — will retry on next run"
      continue
    fi

    if ! declare -f "$fn_name" > /dev/null 2>&1; then
      warn "Migration $basename_m: expected function $fn_name not found — skipping"
      continue
    fi

    if "$fn_name"; then
      echo "$state_key" >> "$state_file"
      applied=$(( applied + 1 ))
      success "Migration applied: $basename_m"
    else
      warn "Migration failed: $basename_m — will retry on next run"
    fi
  done

  if (( applied > 0 )); then
    echo -e "  ${DIM}migrations: $applied applied, $skipped already applied${NC}"
  fi
}

# _prune_stale_migration_state
# Removes entries from the state file that no longer match any discovered migration file.
# This handles direction changes within a PR or cleaned-up old migrations.
_prune_stale_migration_state() {
  local state_file="$MIGRATIONS_STATE_FILE"
  [[ -f "$state_file" ]] || return 0

  # Collect all discovered migration state keys
  local -a discovered_keys=() _migration_dirs=()
  discover_migration_dirs _migration_dirs
  local dir migration basename_m component_rel
  for dir in "${_migration_dirs[@]}"; do
    component_rel="$(dirname "$dir")"
    component_rel="${component_rel#"$WORKBENCH_DIR/"}"
    for migration in "$dir"/*.sh; do
      [[ -f "$migration" ]] || continue
      basename_m="$(basename "$migration")"
      discovered_keys+=("$component_rel/$basename_m")
    done
  done

  # Check each state entry against discovered keys
  local stale_found=false line
  local -a clean_lines=()
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    if _array_contains "$line" "${discovered_keys[@]}"; then
      clean_lines+=("$line")
    else
      warn "Pruned stale migration state: $line"
      stale_found=true
    fi
  done < "$state_file"

  if [[ "$stale_found" == true ]]; then
    printf '%s\n' "${clean_lines[@]}" > "$state_file"
  fi
}

# ─── Adoption of the pre-split root (#624) ───────────────────────────────────
#
# Config, state, and ~200 MB of generated artifacts all used to live in
# ~/.config/workbench. The roots now split three ways, and this carries what a
# machine already has on disk across to them.
#
# It runs ahead of the framework rather than as a migration of its own because
# migrations.applied is one of the files it moves. A migration that moved its
# own bookkeeping would first have to be selected by a framework reading an
# empty state file — which would re-run every past migration alongside it.

# What stays behind in the config root; everything else the legacy root holds
# is state. The list that has to be exhaustive is deliberately the short one:
# the inventory in #624 found four state files that no manifest written in
# advance had thought to list.
readonly _LEGACY_CONFIG_ENTRIES=(
  overrides reuse-level reuse-default review.yml mcp-tools.json
  config.yml config.schema.json
)

# What no root holds any more. A completed migration removed these on purpose,
# and adoption runs ahead of the framework, before any migration reads its own
# bookkeeping — so an entry carried into the state root here is one the
# migration that deleted it is already recorded as applied for, and will never
# run again to clean up after. #730 deletes <state>/logs/ deliberately; without
# this list, a legacy root still holding logs/ would put it back (#732).
#
# Skipped rather than deleted: adoption moves data, it does not decide data is
# worthless, and a legacy root left holding only these says plainly what was
# passed over. It is the counterpart to the list above, and the two together
# are the whole classification — see adopt_legacy_workbench_root.
readonly _LEGACY_UNCLAIMED_ENTRIES=(
  logs
)

# _path_exists PATH — true for anything on disk, a broken symlink included.
_path_exists() {
  [[ -e "$1" || -L "$1" ]]
}

# _is_append_only_ledger SRC DST — true for a pair of files that can be
# concatenated rather than kept side by side.
#
# trail.py and ai_usage.py are the only writers of these, both open them in
# append mode, and otto-log sorts every record by `ts` after loading — so the
# two halves reassemble in any order. Matched by name rather than by the
# .jsonl extension on purpose: the review artifacts (session.jsonl,
# post.jsonl, *.holistic.jsonl) are whole-file writes whose convention is
# prior-content-first, and splicing two runs together would misreport both.
_is_append_only_ledger() {
  local src="$1" dst="$2" parent
  if [[ ! -f "$src" || -L "$src" || ! -f "$dst" || -L "$dst" ]]; then
    return 1
  fi
  if [[ "${src##*/}" == "trail.jsonl" ]]; then
    return 0
  fi
  parent="${src%/*}"
  if [[ "${parent##*/}" == "usage" && "$src" == *.jsonl ]]; then
    return 0
  fi
  return 1
}

# _append_ledger SRC DST — fold an append-only ledger into its successor.
# Builds the merged result in a temp file and swaps it in with `mv` so a
# failure partway through never leaves DST with a partial, unrepeatable
# append — a retry sees the original DST and SRC untouched.
_append_ledger() {
  local src="$1" dst="$2" tmp mode
  tmp="$(mktemp "${dst}.XXXXXX")" || {
    warn "Could not create a temp file to merge $src into $dst"
    return 1
  }
  # mktemp defaults to 0600; every other _adopt_entry path preserves the
  # original file's mode via a plain mv, so this one should too.
  mode=$(file_mode "$dst") && chmod "$mode" "$tmp"
  if ! cat "$dst" > "$tmp"; then
    warn "Could not read $dst — left $src in place"
    rm -f "$tmp"
    return 1
  fi
  # A destination that does not end in a newline would fuse its last record
  # with the first one appended after it, and the reader silently drops lines
  # it cannot parse.
  if [[ -s "$tmp" && -n "$(tail -c 1 "$tmp")" ]]; then
    printf '\n' >> "$tmp"
  fi
  if ! cat "$src" >> "$tmp"; then
    warn "Could not append $src to $dst — left it in place"
    rm -f "$tmp"
    return 1
  fi
  if ! mv "$tmp" "$dst"; then
    warn "Could not replace $dst with the merged ledger — left $src in place"
    rm -f "$tmp"
    return 1
  fi
  rm -f "$src"
  return 0
}

# _adopt_entry SRC DST — carry one entry across, resuming a partial run.
# Hand-rolled rather than `rsync -a --remove-source-files`: rsync is not a
# workbench dependency, and this runs on a machine mid-sync that may not have it.
# Tallies _ADOPT_MOVED/_ADOPT_STAYED at each leaf decision (rather than letting
# the caller count by top-level entry) so a directory merge with some children
# moved and one failed is not misreported as a single entry that stayed.
_adopt_entry() {
  local src="$1" dst="$2"

  if ! _path_exists "$dst"; then
    mkdir -p "$(dirname "$dst")"
    if ! mv "$src" "$dst"; then
      warn "Could not move $src to $dst — left it in place"
      _ADOPT_STAYED=$(( _ADOPT_STAYED + 1 ))
      return 1
    fi
    _ADOPT_MOVED=$(( _ADOPT_MOVED + 1 ))
    return 0
  fi

  # A destination that already exists is either a run that was interrupted
  # partway through 200 MB or a directory the new root already had. Merging
  # child by child finishes what the first run started; a plain mv would fail
  # or nest the source inside the destination.
  if [[ -d "$src" && ! -L "$src" && -d "$dst" && ! -L "$dst" ]]; then
    _adopt_dir "$src" "$dst"
    return $?
  fi

  # A ledger both roots hold is not a conflict — it is one history in two
  # files, and keeping both would hide the older one from every reader.
  if _is_append_only_ledger "$src" "$dst"; then
    if _append_ledger "$src" "$dst"; then
      _ADOPT_MOVED=$(( _ADOPT_MOVED + 1 ))
      return 0
    fi
    _ADOPT_STAYED=$(( _ADOPT_STAYED + 1 ))
    return 1
  fi

  warn "Both $src and $dst exist — kept the new one; reconcile and remove the old"
  _ADOPT_STAYED=$(( _ADOPT_STAYED + 1 ))
  return 1
}

# _adopt_dir SRC DST — merge SRC into an existing DST, entry by entry.
_adopt_dir() {
  local src="$1" dst="$2" child failed=0
  # Dotfiles are skipped at the top level, where the only ones that turn up are
  # the filesystem's own, but a review or a log directory can legitimately hold
  # one — leaving it behind would strand data and block the rmdir below.
  local restore_dotglob=false
  shopt -q dotglob || restore_dotglob=true
  shopt -s dotglob

  for child in "$src"/*; do
    _path_exists "$child" || continue
    if ! _adopt_entry "$child" "$dst/${child##*/}"; then
      failed=1
    fi
  done

  if [[ "$restore_dotglob" == true ]]; then
    shopt -u dotglob
  fi
  # Only an empty directory goes; whatever could not move keeps its home.
  rmdir "$src" 2>/dev/null || true
  return "$failed"
}

# adopt_legacy_workbench_root
# Move a pre-#624 ~/.config/workbench to whichever roots now own its contents.
# No-op once the legacy root is gone, or when a root still resolves to it.
adopt_legacy_workbench_root() {
  local legacy="$LEGACY_WORKBENCH_ROOT"
  [[ -d "$legacy" ]] || return 0

  local entry name target
  # _adopt_entry tallies these itself, down to the leaf, so a partial
  # directory merge is reflected in the counts instead of collapsing to one
  # failed top-level entry.
  _ADOPT_MOVED=0
  _ADOPT_STAYED=0
  _ADOPT_UNCLAIMED=0
  # Dotfiles are left where they are: the only ones that turn up are the
  # filesystem's own (.DS_Store), and they belong to no root.
  for entry in "$legacy"/*; do
    _path_exists "$entry" || continue
    name="${entry##*/}"
    # All three destinations are named, none of them left to fall out of the
    # others. The state root is still where an unlisted entry goes, and that is
    # deliberate — #624's inventory found four state files that no manifest
    # written in advance had thought to list, so the list that has to be
    # exhaustive is the config one. What it must not also absorb is a name no
    # root holds any more, because the state root is one other code prunes.
    if _array_contains "$name" "${_LEGACY_CONFIG_ENTRIES[@]}"; then
      target="$WORKBENCH_CONFIG_DIR"
    elif _array_contains "$name" "${_LEGACY_UNCLAIMED_ENTRIES[@]}"; then
      target=""
    else
      target="$WORKBENCH_STATE_DIR"
    fi
    if [[ -z "$target" ]]; then
      _ADOPT_UNCLAIMED=$(( _ADOPT_UNCLAIMED + 1 ))
      continue
    fi
    if [[ "$target" == "$legacy" ]]; then
      continue
    fi
    _adopt_entry "$entry" "$target/$name"
  done

  if (( _ADOPT_MOVED > 0 )); then
    # No destination named: config entries and state entries went to different
    # roots, and on an overridden machine both of those moved.
    success "Adopted $_ADOPT_MOVED entries from $legacy"
  fi
  # The sync that runs this is usually the unattended one from the maintenance
  # agent, whose output goes to a log file. A partial adoption has to state
  # itself rather than be inferred from the warnings scattered above it.
  if (( _ADOPT_STAYED > 0 )); then
    warn "$_ADOPT_STAYED entries could not be adopted — $legacy still holds them"
  fi
  # Said every run, for the same reason as the line above: the operator reading
  # a sync log is the only one who can decide these are finished with, and the
  # notice is what stops $legacy from lingering unexplained. It ends the moment
  # they delete them.
  if (( _ADOPT_UNCLAIMED > 0 )); then
    warn "$_ADOPT_UNCLAIMED entries in $legacy belong to no root — left in place; delete them when you no longer want them"
  fi
  rmdir "$legacy" 2>/dev/null || true
  return 0
}

# run_all_migrations
# Discovers and runs migrations across all components, then prunes stale state.
run_all_migrations() {
  # Before anything reads the state root: carry a pre-split ~/.config/workbench
  # into the roots that own it now, migrations.applied included.
  adopt_legacy_workbench_root

  # Prune stale state entries before running (handles removed/renamed migrations)
  _prune_stale_migration_state

  local -a _migration_dirs=()
  discover_migration_dirs _migration_dirs

  if [[ ${#_migration_dirs[@]} -eq 0 ]]; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}no migrations found${NC}" || true
    return
  fi

  local dir
  for dir in "${_migration_dirs[@]}"; do
    run_component_migrations "$(dirname "$dir")"
  done
}
