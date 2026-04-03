#!/usr/bin/env bash
# Component discovery helpers — single source of truth for finding steps.sh
# and migration directories across the workbench.
#
# All discovery uses the same two-level glob: top-level dirs + one level of nesting.
# Adding a new component tier (e.g. editors/zed/) is automatically discovered.
#
# Usage (from scripts that already source lib/ui.sh):
#   . "$WORKBENCH_DIR/lib/components.sh"
#   discover_step_files  _steps_arr    # populates array with steps.sh paths
#   discover_migration_dirs _dirs_arr  # populates array with migration dir paths

# Guard: constants must be loaded (provides WORKBENCH_DIR)
if [[ -z "${WORKBENCH_DIR:-}" ]]; then
  echo "ERROR: lib/components.sh requires WORKBENCH_DIR (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

# discover_step_files ARRAY_REF
# Populates the nameref array with paths to all steps.sh files:
#   $WORKBENCH_DIR/*/steps.sh and $WORKBENCH_DIR/*/*/steps.sh
# shellcheck disable=SC2178  # __out is a nameref to an array — shellcheck misreads it
discover_step_files() {
  local -n __out=$1
  __out=()
  local file
  for file in "$WORKBENCH_DIR"/*/steps.sh "$WORKBENCH_DIR"/*/*/steps.sh; do
    [[ -f "$file" ]] && __out+=("$file")
  done
  return 0
}

# discover_migration_dirs ARRAY_REF
# Populates the nameref array with paths to all migration directories:
#   $WORKBENCH_DIR/*/migrations and $WORKBENCH_DIR/*/*/migrations
# shellcheck disable=SC2178  # __out is a nameref to an array — shellcheck misreads it
discover_migration_dirs() {
  local -n __out=$1
  __out=()
  local dir
  for dir in "$WORKBENCH_DIR"/*/migrations "$WORKBENCH_DIR"/*/*/migrations; do
    [[ -d "$dir" ]] && __out+=("$dir")
  done
  return 0
}
