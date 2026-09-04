#!/usr/bin/env bash
# Migration: fold the scattered config files into one config.yml.
#
# reuse-level, reuse-default and review.yml each picked their own format. This
# folds whichever exist into the single config.yml that ai/lib/config/workbench_config.py
# types and config.schema.json describes, then renames the originals so a
# re-run has nothing left to read.
#
# Runs after adopt_legacy_workbench_root, so a pre-split machine's files are
# already under the config root by the time this reads them.
#
# adoption-sensitive: those three names are _LEGACY_CONFIG_ENTRIES, so adoption
# writes them straight back into the config root this drains. It only bites on a
# machine that overrides XDG_CONFIG_HOME — elsewhere the config root and the
# legacy root are the same path and adoption skips them — but the resolution is
# the same either way: another pass folds them in.

# Carry one single-line file into a config.yml key.
#
# A key config.yml already holds wins: /reuse writes straight to config.yml, so
# on a machine where the hook ran before the first sync the file is the newer
# answer and the legacy scrap is stale. The source is renamed either way — it
# is dead once the key has a value, whichever write put it there.
#
# Returns non-zero without renaming when yq fails, so the migration is not
# recorded and the next sync tries again against an untouched source.
_unify_workbench_config_fold_scalar() {
  local source_file="$1" key="$2"
  [[ -f "$source_file" ]] || return 0

  local existing value
  existing="$(yq "$key // \"\"" "$WORKBENCH_CONFIG_FILE")" || return 1
  if [[ -z "$existing" ]]; then
    value="$(tr -d '[:space:]' < "$source_file")" || return 1
    v="$value" yq -i "$key = strenv(v)" "$WORKBENCH_CONFIG_FILE" || return 1
  fi

  mv "$source_file" "$source_file.migrated"
}

# Carry review.yml's issue_tracker mapping into .issue_tracker.
#
# Same precedence and same rename contract as the scalar folder above. A
# review.yml holding no issue_tracker has nothing to carry, so it is only
# renamed — writing its null over the key would erase a real setting.
#
# This wrote .review.issue_tracker until the key moved to the top level; a
# machine that ran that version is carried across by
# 20260824-lift-issue-tracker-key rather than by a second path here.
_unify_workbench_config_fold_review() {
  local source_file="$1"
  [[ -f "$source_file" ]] || return 0

  local existing incoming
  existing="$(yq '.issue_tracker // ""' "$WORKBENCH_CONFIG_FILE")" || return 1
  incoming="$(yq '.issue_tracker // ""' "$source_file")" || return 1
  if [[ -z "$existing" && -n "$incoming" ]]; then
    # load() rather than a rendered value, so the mapping lands in block style
    # like the rest of a file the user is expected to hand-edit. The path goes
    # through strenv for the same reason the scalar folder passes its value that
    # way: nothing this script controls is spliced into a yq expression.
    src="$source_file" yq -i \
      '.issue_tracker = load(strenv(src)).issue_tracker' \
      "$WORKBENCH_CONFIG_FILE" || return 1
  fi

  mv "$source_file" "$source_file.migrated"
}

migration_20260814_unify_workbench_config() {
  local level_file="$WORKBENCH_CONFIG_DIR/reuse-level"
  local default_file="$WORKBENCH_CONFIG_DIR/reuse-default"
  local review_file="$WORKBENCH_CONFIG_DIR/review.yml"

  # NOOP rather than deferred: nothing writes these three any more — /reuse and
  # the review settings go to config.yml — so a machine without them will not
  # grow them on its own. Adoption is the one way a pre-split copy arrives late,
  # and the marker above hands that case to
  # _forget_adoption_sensitive_migrations rather than to a retry.
  if [[ ! -f "$level_file" && ! -f "$default_file" && ! -f "$review_file" ]]; then
    return "$MIGRATION_NOOP"
  fi

  info "Unifying workbench settings into config.yml"
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  wb_config_ensure_file "$WORKBENCH_CONFIG_FILE" || return 1

  local failed=0
  _unify_workbench_config_fold_scalar "$level_file" .reuse.level || failed=1
  _unify_workbench_config_fold_scalar "$default_file" .reuse.default || failed=1
  _unify_workbench_config_fold_review "$review_file" || failed=1

  if (( failed )); then
    # Only the failing fold's source is untouched — a fold that ran before it
    # has already renamed its own, so the message points at the .migrated files
    # rather than claiming every original is still there.
    warn "Could not fold every setting into config.yml — check the .migrated files for what carried over"
    return 1
  fi

  success "Workbench settings unified into config.yml"
  return 0
}
