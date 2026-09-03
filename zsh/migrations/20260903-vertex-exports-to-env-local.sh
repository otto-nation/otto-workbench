#!/usr/bin/env bash
# Migration: move the Vertex and Claude Code exports out of ~/.zshrc into
# ~/.env.local, which the loader sources ahead of every config layer.
#
# ~/.zshrc sources the loader near the top and reserves everything below it for
# machine-specific config, so a value exported there is set *after* the layers
# have run. zsh/config.d/tools/vertex.zsh reads ANTHROPIC_VERTEX_PROJECT_ID to
# mirror it into GOOGLE_CLOUD_PROJECT for Pi's Vertex provider, and found
# nothing: the export it needed was seventeen lines further down the file. The
# shim now retries at the first prompt, but a layer that wants a value still
# cannot see one set below it, and ai/claude/steps.sh mirrors these same
# variables into ~/.claude/settings.json from ~/.env.local alone.

migration_20260903_vertex_exports_to_env_local() {
  local zshrc="$ZSHRC_FILE"
  # No ~/.zshrc means no hand-written block to move, and the one step_zshrc
  # writes for a fresh machine carries none — nothing arrives later to revisit.
  [[ -f "$zshrc" ]] || return "$MIGRATION_NOOP"

  # Frozen at this migration's date on purpose. It relocates the values a
  # machine already has; a variable a registry gains afterwards is one nobody
  # ever wrote into ~/.zshrc by hand.
  local -a vars=(
    CLAUDE_CODE_USE_VERTEX
    CLOUD_ML_REGION
    ANTHROPIC_VERTEX_PROJECT_ID
    ANTHROPIC_MODEL
    ANTHROPIC_DEFAULT_OPUS_MODEL
    ANTHROPIC_DEFAULT_SONNET_MODEL
    ANTHROPIC_DEFAULT_HAIKU_MODEL
  )

  local drop_file append_file comment_file
  drop_file=$(mktemp)
  append_file=$(mktemp)
  comment_file=$(mktemp)
  # shellcheck disable=SC2064  # the paths must expand now, not at trap time
  trap "rm -f '$drop_file' '$append_file' '$comment_file'" RETURN

  local -a superseded=()
  local var zshrc_line env_line
  for var in "${vars[@]}"; do
    zshrc_line=$(grep -m1 "^export ${var}=" "$zshrc") || continue

    env_line=""
    if [[ -f "$ENV_LOCAL_FILE" ]]; then
      env_line=$(grep -m1 "^export ${var}=" "$ENV_LOCAL_FILE") || env_line=""
    fi

    # Already in ~/.env.local with the same value: the ~/.zshrc copy is the
    # redundant one and only drops out.
    if [[ "$env_line" == "$zshrc_line" ]]; then
      printf '%s\n' "$zshrc_line" >> "$drop_file"
      continue
    fi

    # Set in both with different values. ~/.zshrc is read after ~/.env.local, so
    # its value is the one in effect today and carrying it over is what keeps
    # the shell answering the same afterwards. Leaving the two to be reconciled
    # by hand is what this cannot do: the migration is recorded either way and
    # never asks again, so a warning nobody acts on is a conflict that outlives
    # every later sync. The displaced line is commented out rather than deleted.
    if [[ -n "$env_line" ]]; then
      printf '%s\n' "$env_line" >> "$comment_file"
      superseded+=("$var")
    fi

    printf '%s\n' "$zshrc_line" >> "$drop_file"
    printf '%s\n' "$zshrc_line" >> "$append_file"
  done

  [[ -s "$drop_file" ]] || return "$MIGRATION_NOOP"

  # Values to relocate but nowhere yet to put them: step_env_local creates
  # ~/.env.local from the template later in this same sync. There is nothing to
  # weigh against $append_file here — with no ~/.env.local to compare against,
  # every line that drops out of ~/.zshrc is also one to write.
  if [[ ! -f "$ENV_LOCAL_FILE" ]]; then
    return "$MIGRATION_DEFERRED"
  fi

  # Comment the displaced lines out before appending, so the pass never sees a
  # line this migration is about to write.
  if [[ -s "$comment_file" ]]; then
    local comment_program='
      BEGIN { while ((getline line < mf) > 0) target[line] = 1 }
      $0 in target { print "# superseded by the same export moved out of ~/.zshrc, which was read later: " $0; next }
      { print }
    '
    if ! _vertex_exports_rewrite "$ENV_LOCAL_FILE" "$comment_program" "$comment_file"; then
      err "Could not rewrite $ENV_LOCAL_FILE — exports left where they are"
      return 1
    fi
  fi

  if [[ -s "$append_file" ]]; then
    local header="# Moved here from ~/.zshrc by otto-workbench — the config layers are sourced before ~/.zshrc's own block."
    if ! grep -qxF "$header" "$ENV_LOCAL_FILE"; then
      printf '\n%s\n' "$header" >> "$ENV_LOCAL_FILE"
    fi
    cat "$append_file" >> "$ENV_LOCAL_FILE"
  fi

  local drop_program='
    BEGIN { while ((getline line < mf) > 0) target[line] = 1 }
    $0 in target { next }
    { print }
  '
  if ! _vertex_exports_rewrite "$zshrc" "$drop_program" "$drop_file"; then
    err "Could not rewrite $zshrc — its exports now duplicate $ENV_LOCAL_FILE"
    return 1
  fi

  local moved redundant
  moved=$(wc -l < "$append_file" | tr -d ' ')
  redundant=$(( $(wc -l < "$drop_file" | tr -d ' ') - moved ))
  if [[ "$moved" -gt 0 ]]; then
    info "Moved $moved export(s) from ~/.zshrc to $ENV_LOCAL_FILE"
  fi
  if [[ "$redundant" -gt 0 ]]; then
    info "Dropped $redundant export(s) from ~/.zshrc that $ENV_LOCAL_FILE already set"
  fi
  if [[ ${#superseded[@]} -gt 0 ]]; then
    warn "Set in both files — ~/.zshrc's value was the one in effect and is the one kept:"
    for var in "${superseded[@]}"; do
      info "  $var — the $ENV_LOCAL_FILE line it replaces is commented out above it"
    done
  fi
}

# _vertex_exports_rewrite FILE PROGRAM MAP_FILE — replaces FILE with the output
# of the awk PROGRAM run over it, which reads MAP_FILE as `mf`.
#
# mktemp answers 0600 and these are the operator's own dotfiles — ~/.zshrc is
# ordinarily world-readable — so the mode is carried across rather than
# inherited from the replacement.
_vertex_exports_rewrite() {
  local file="$1" program="$2" map_file="$3"

  local mode tmp
  mode=$(file_mode "$file")
  tmp=$(mktemp)
  if ! awk -v mf="$map_file" "$program" "$file" > "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$file"
  chmod "$mode" "$file"
}
