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
#     its own function is the way that happens in practice.
#     validate-migrations rejects that shape now, but the framework must hold
#     even for a file the validator never saw
#   - the `set -e` itself, which outlives the source and would otherwise arm
#     errexit for every component that syncs after this one
#
# So the file is read twice, and the two passes answer different questions.
#
# The verdict pass runs the file in a fresh `bash -e`. Nothing else in this
# shell can reach it: bash ignores errexit for everything a compound command or
# function runs when the caller's own context ignores it, and that suppression
# is inherited through function calls and subshells alike — so an in-process
# `( set -e; . "$migration" )` is silently disarmed the moment any caller up the
# chain is an `if`, a `!`, or an `||`, which is exactly the call shape below. A
# separate process starts from its own errexit state and cannot be disarmed that
# way, so a file-scope statement that fails stops the file there and is reported
# here. An ERR trap is not an alternative: the same rule suppresses it.
#
# The load pass then sources the file for real, for its definitions. Its `set -e`
# is neutralised by the `||`, and the caller's own setting is put back exactly as
# found afterwards, so nothing the file does at file scope changes how the rest
# of the sync runs. A file that got this far already ran clean once, so the `||`
# is not swallowing a verdict — it only covers the case where the two passes
# disagree because the fresh process lacked this shell's variables. The caller
# checks that the expected function exists, which catches that.
#
# ceiling: a file with real work at file scope does that work twice. Only files
# the validator would reject can be in that shape, and migrations must be
# idempotent anyway; revisit if file-scope work ever becomes legitimate.
_source_migration() {
  local migration="$1" status=0 errexit_on=false
  [[ $- != *e* ]] || errexit_on=true

  "${BASH:-bash}" -e -c '. "$1"' _ "$migration"
  status=$?

  if (( status == 0 )); then
    # shellcheck source=/dev/null
    . "$migration" || true
  fi

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

# _migration_carries_marker FILE [MARKER_RE]
# True when no marker was asked for, or when FILE's text carries it.
_migration_carries_marker() {
  [[ -z "$2" ]] && return 0
  grep -qE "$2" "$1"
}

# _discover_migration_keys OUT_ARRAY [MARKER_RE]
# Collect the state keys of every discovered migration, in the same
# "<component>/<basename>.sh" form run_component_migrations records. With
# MARKER_RE given, only the migrations whose file matches it are collected.
_discover_migration_keys() {
  local -n __keys="$1"
  local marker_re="${2:-}"
  __keys=()

  local -a _migration_dirs=()
  discover_migration_dirs _migration_dirs
  local dir migration component_rel
  for dir in "${_migration_dirs[@]}"; do
    component_rel="$(dirname "$dir")"
    component_rel="${component_rel#"$WORKBENCH_DIR/"}"
    for migration in "$dir"/*.sh; do
      [[ -f "$migration" ]] || continue
      _migration_carries_marker "$migration" "$marker_re" || continue
      __keys+=("$component_rel/${migration##*/}")
    done
  done
}

# _forget_adoption_sensitive_migrations
# Drop the state entries of every migration marked adoption-sensitive, so the
# framework runs them again over the data adoption has just moved into place.
#
# Scoped to the marked migrations rather than clearing the file: a migration
# that removed something on purpose (an MCP entry, ~/.kiro) is idempotent in the
# sense the framework asks for — re-running it produces the same result — but
# that result is "gone again", which would undo an operator who deliberately put
# it back. Only a migration that says adoption can undo it gets another pass.
_forget_adoption_sensitive_migrations() {
  local state_file="$MIGRATIONS_STATE_FILE"
  [[ -f "$state_file" ]] || return 0

  local -a sensitive_keys=()
  _discover_migration_keys sensitive_keys "$_ADOPTION_SENSITIVE_MARKER"
  (( ${#sensitive_keys[@]} > 0 )) || return 0

  local line forgotten=0
  local -a kept=()
  # `|| [[ -n "$line" ]]`: read reports EOF for a final line with no newline
  # after it, and the loop body would never see it — an entry silently dropped
  # from the state file, which is the same class of loss this function exists
  # to close. Every writer here terminates its lines; a hand-edited file does
  # not have to.
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    if _array_contains "$line" "${sensitive_keys[@]}"; then
      forgotten=$(( forgotten + 1 ))
      continue
    fi
    kept+=("$line")
  done < "$state_file"

  (( forgotten > 0 )) || return 0
  if (( ${#kept[@]} > 0 )); then
    printf '%s\n' "${kept[@]}" > "$state_file"
  else
    : > "$state_file"
  fi
  info "$forgotten adoption-sensitive migration(s) will run again — adoption moved data back into what they drain"
}

# _prune_stale_migration_state
# Removes entries from the state file that no longer match any discovered migration file.
# This handles direction changes within a PR or cleaned-up old migrations.
_prune_stale_migration_state() {
  local state_file="$MIGRATIONS_STATE_FILE"
  [[ -f "$state_file" ]] || return 0

  local -a discovered_keys=()
  _discover_migration_keys discovered_keys

  # Check each state entry against discovered keys
  local stale_found=false line
  local -a clean_lines=()
  # Same unterminated-last-line guard as _forget_adoption_sensitive_migrations.
  while IFS= read -r line || [[ -n "$line" ]]; do
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

# ─── Adoption of the pre-split root ──────────────────────────────────────────
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
# an inventory of the legacy root found four state files that no manifest
# written in advance had thought to list.
readonly _LEGACY_CONFIG_ENTRIES=(
  overrides reuse-level reuse-default review.yml mcp-tools.json
  config.yml config.schema.json
)

# What no root holds any more. A completed migration removed these on purpose,
# and adoption runs ahead of the framework, before any migration reads its own
# bookkeeping — so an entry carried into the state root here is one the
# migration that deleted it is already recorded as applied for, and will never
# run again to clean up after. <state>/logs/ is deleted deliberately; without
# this list, a legacy root still holding logs/ would put it back.
#
# Skipped rather than deleted: adoption moves data, it does not decide data is
# worthless, and a legacy root left holding only these says plainly what was
# passed over. It is the counterpart to the list above, and the two together
# are the whole classification — see adopt_legacy_workbench_root.
readonly _LEGACY_UNCLAIMED_ENTRIES=(
  logs
)

# The header line a migration writes to say adoption can put its work back.
#
# The two lists above classify a legacy entry by name; this classifies the
# *migrations* by what they drain. A migration that empties a path under a root
# adoption writes into is undone by an adoption that runs later: the entry lands
# in that path again, and the state file already records the migration as
# applied, so nothing ever drains it a second time. The trail root has that
# shape — reviews/<x>/trail.jsonl re-seeded under the state root after
# 20260814-unify-trail-root drained it, where otto-log's flat glob of the trail
# root cannot see it. The trail is not lost, it is permanently invisible.
#
# Declared by the migration rather than listed here so the two cannot drift: a
# list of state keys would go stale the moment a migration file is renamed, and
# the marker travels with the file. Adding one is the whole opt-in — no registry
# and no edit to this file.
readonly _ADOPTION_SENSITIVE_MARKER='^# adoption-sensitive:'

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
# Move a pre-split ~/.config/workbench to whichever roots now own its contents.
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
    # deliberate — the legacy inventory found four state files that no manifest
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
    # Here rather than in run_all_migrations: migrations.applied is itself one
    # of the entries the loop above may have just moved, so this is the first
    # point at which the state root holds the file that has to be edited.
    _forget_adoption_sensitive_migrations
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
# Adopts the legacy root, backfills the project registry, prunes stale state, then runs every component's migrations.
run_all_migrations() {
  # Before anything reads the state root: carry a pre-split ~/.config/workbench
  # into the roots that own it now, migrations.applied included.
  adopt_legacy_workbench_root

  # Before any project-scoped migration reads it: backfill the registry of repos
  # that use the workbench. Here rather than as a migration of its own because
  # migrations run in filename order — one that sorted ahead of the backfill
  # would read an empty registry, find nothing, and record itself as applied.
  # No-op after the first run on a machine.
  seed_project_registry

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
