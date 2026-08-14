#!/usr/bin/env bash
# Migration: fold the scattered config files into one config.yml.
#
# reuse-level, reuse-default and review.yml each picked their own format. This
# folds whichever exist into the single config.yml that ai/lib/workbench_config.py
# types and config.schema.json describes, then renames the originals so a
# re-run has nothing left to read.
#
# Runs after adopt_legacy_workbench_root, so a pre-#624 machine's files are
# already under the config root by the time this reads them.

migration_20260814_unify_workbench_config() {
  [[ -f "$WORKBENCH_CONFIG_FILE" ]] && return 0

  local level_file="$WORKBENCH_CONFIG_DIR/reuse-level"
  local default_file="$WORKBENCH_CONFIG_DIR/reuse-default"
  local review_file="$WORKBENCH_CONFIG_DIR/review.yml"

  local -a sources=()
  [[ -f "$level_file" ]] && sources+=("$level_file")
  [[ -f "$default_file" ]] && sources+=("$default_file")
  [[ -f "$review_file" ]] && sources+=("$review_file")
  [[ ${#sources[@]} -eq 0 ]] && return 0

  info "Unifying workbench settings into config.yml"
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  echo "{}" > "$WORKBENCH_CONFIG_FILE"

  local value
  if [[ -f "$level_file" ]]; then
    value="$(tr -d '[:space:]' < "$level_file")"
    v="$value" yq -i '.reuse.level = strenv(v)' "$WORKBENCH_CONFIG_FILE"
  fi
  if [[ -f "$default_file" ]]; then
    value="$(tr -d '[:space:]' < "$default_file")"
    v="$value" yq -i '.reuse.default = strenv(v)' "$WORKBENCH_CONFIG_FILE"
  fi
  if [[ -f "$review_file" ]]; then
    yq -i ".review.issue_tracker = load(\"$review_file\").issue_tracker" \
      "$WORKBENCH_CONFIG_FILE"
  fi

  local source
  for source in "${sources[@]}"; do
    mv "$source" "$source.migrated"
  done

  success "Workbench settings unified into config.yml"
  return 0
}
