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

# Guard: constants must be loaded (provides WORKBENCH_DIR, MIGRATIONS_STATE_FILE, etc.)
if [[ -z "${WORKBENCH_DIR:-}" ]]; then
  echo "ERROR: lib/migrations.sh requires WORKBENCH_DIR (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

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

  if (( applied > 0 || skipped > 0 )); then
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
  local -a discovered_keys=()
  local dir migration basename_m component_rel
  for dir in "$WORKBENCH_DIR"/*/migrations "$WORKBENCH_DIR"/*/*/migrations; do
    [[ -d "$dir" ]] || continue
    component_rel="$(dirname "$dir")"
    component_rel="${component_rel#"$WORKBENCH_DIR/"}"
    for migration in "$dir"/*.sh; do
      [[ -f "$migration" ]] || continue
      basename_m="$(basename "$migration")"
      discovered_keys+=("$component_rel/$basename_m")
    done
  done

  # Check each state entry against discovered keys
  local stale_found=false line found key
  local -a clean_lines=()
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    found=false
    for key in "${discovered_keys[@]}"; do
      if [[ "$line" == "$key" ]]; then
        found=true
        break
      fi
    done
    if [[ "$found" == true ]]; then
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

# run_all_migrations
# Discovers and runs migrations across all components, then prunes stale state.
run_all_migrations() {
  # Prune stale state entries before running (handles removed/renamed migrations)
  _prune_stale_migration_state

  local found=false dir
  for dir in "$WORKBENCH_DIR"/*/migrations "$WORKBENCH_DIR"/*/*/migrations; do
    [[ -d "$dir" ]] || continue
    found=true
    run_component_migrations "$(dirname "$dir")"
  done

  if [[ "$found" == false ]]; then
    echo -e "  ${DIM}no migrations found${NC}"
  fi
}
