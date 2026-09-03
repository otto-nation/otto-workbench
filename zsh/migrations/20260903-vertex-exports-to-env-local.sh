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

  local drop_file append_file
  drop_file=$(mktemp)
  append_file=$(mktemp)
  # shellcheck disable=SC2064  # both paths must expand now, not at trap time
  trap "rm -f '$drop_file' '$append_file'" RETURN

  local -a conflicts=()
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

    # A *different* value is not this migration's to resolve — today the
    # ~/.zshrc export runs last and wins, so deleting it would silently change
    # which value the shell ends up with.
    if [[ -n "$env_line" ]]; then
      conflicts+=("$var")
      continue
    fi

    printf '%s\n' "$zshrc_line" >> "$drop_file"
    printf '%s\n' "$zshrc_line" >> "$append_file"
  done

  if [[ ${#conflicts[@]} -gt 0 ]]; then
    warn "Set in both ~/.zshrc and $ENV_LOCAL_FILE with different values — left in ~/.zshrc:"
    for var in "${conflicts[@]}"; do
      info "  $var"
    done
    info "  Delete whichever copy is stale; the ~/.zshrc one currently wins."
  fi

  [[ -s "$drop_file" ]] || return "$MIGRATION_NOOP"

  # Values to relocate but nowhere yet to put them: step_env_local creates
  # ~/.env.local from the template later in this same sync. There is nothing to
  # weigh against $append_file here — with no ~/.env.local to compare against,
  # every line that drops out of ~/.zshrc is also one to write.
  if [[ ! -f "$ENV_LOCAL_FILE" ]]; then
    return "$MIGRATION_DEFERRED"
  fi

  if [[ -s "$append_file" ]]; then
    local header="# Moved here from ~/.zshrc by otto-workbench — the config layers are sourced before ~/.zshrc's own block."
    if ! grep -qxF "$header" "$ENV_LOCAL_FILE"; then
      printf '\n%s\n' "$header" >> "$ENV_LOCAL_FILE"
    fi
    cat "$append_file" >> "$ENV_LOCAL_FILE"
  fi

  # mktemp answers 0600 and ~/.zshrc is ordinarily world-readable, so the mode
  # is carried across rather than inherited from the replacement.
  local mode tmp
  mode=$(file_mode "$zshrc")
  tmp=$(mktemp)
  if ! awk -v df="$drop_file" '
    BEGIN { while ((getline line < df) > 0) drop[line] = 1 }
    $0 in drop { next }
    { print }
  ' "$zshrc" > "$tmp"; then
    rm -f "$tmp"
    err "Could not rewrite $zshrc — exports left where they are"
    return 1
  fi
  mv "$tmp" "$zshrc"
  chmod "$mode" "$zshrc"

  local moved
  moved=$(wc -l < "$drop_file" | tr -d ' ')
  info "Moved $moved export(s) from ~/.zshrc to $ENV_LOCAL_FILE"
}
