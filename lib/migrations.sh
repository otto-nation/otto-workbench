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

    # shellcheck source=/dev/null
    . "$migration"

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
_LEGACY_CONFIG_ENTRIES=(
  overrides reuse-level reuse-default review.yml mcp-tools.json
  config.yml config.schema.json
)

# _path_exists PATH — true for anything on disk, a broken symlink included.
_path_exists() {
  [[ -e "$1" || -L "$1" ]]
}

# _adopt_entry SRC DST — carry one entry across, resuming a partial run.
_adopt_entry() {
  local src="$1" dst="$2"

  if ! _path_exists "$dst"; then
    mkdir -p "$(dirname "$dst")"
    if ! mv "$src" "$dst"; then
      warn "Could not move $src to $dst — left it in place"
      return 1
    fi
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

  warn "Both $src and $dst exist — kept the new one; reconcile and remove the old"
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

  local docker_aliases="$legacy/docker-aliases.zsh" had_docker=false
  if _path_exists "$docker_aliases"; then
    had_docker=true
  fi

  local entry name target moved=0
  # Dotfiles are left where they are: the only ones that turn up are the
  # filesystem's own (.DS_Store), and they belong to no root.
  for entry in "$legacy"/*; do
    _path_exists "$entry" || continue
    name="${entry##*/}"
    target="$WORKBENCH_STATE_DIR"
    if _array_contains "$name" "${_LEGACY_CONFIG_ENTRIES[@]}"; then
      target="$WORKBENCH_CONFIG_DIR"
    fi
    if [[ "$target" == "$legacy" ]]; then
      continue
    fi
    if _adopt_entry "$entry" "$target/$name"; then
      moved=$(( moved + 1 ))
    fi
  done

  if (( moved == 0 )); then
    return 0
  fi

  success "Adopted $moved entries from $legacy into $WORKBENCH_STATE_DIR"
  if [[ "$had_docker" == true ]] && ! _path_exists "$docker_aliases"; then
    warn "Shells already running still source the old docker-aliases.zsh — open a new one"
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
