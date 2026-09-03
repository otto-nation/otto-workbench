#!/usr/bin/env bash
# ~/.claude/rules/ was both an install output and the only home for the two rule
# layers that exist per machine, which is why a machine without Claude Code had
# nowhere to keep them. The layers move out: hand-written *.local.md files join
# the operator's other overrides, and the generated workbench.md is rewritten
# under the state root on every sync, so the copy here is dropped rather than
# moved. What stays behind is output — symlinks step_claude_rules re-creates.

migration_20260903_local_rules_to_overrides() {
  [[ -d "$CLAUDE_RULES_DIR" ]] || return "$MIGRATION_NOOP"

  local moved=0 item name dest

  # Only regular files: a *.local.md symlink here already points at a layer
  # source, so it is an install this migration must not consume.
  for item in "$CLAUDE_RULES_DIR"/*.local.md; do
    [[ -f "$item" && ! -L "$item" ]] || continue
    name=$(basename "$item")
    dest="$USER_RULES_DIR/$name"
    if [[ -e "$dest" ]]; then
      err "Both $item and $dest exist — merge them by hand, then re-run sync"
      return 1
    fi
    mkdir -p "$USER_RULES_DIR"
    mv "$item" "$dest"
    moved=$(( moved + 1 ))
  done

  local generated="$CLAUDE_RULES_DIR/workbench.md"
  if [[ -f "$generated" && ! -L "$generated" ]]; then
    rm "$generated"
    moved=$(( moved + 1 ))
  fi

  (( moved > 0 )) || return "$MIGRATION_NOOP"
  return 0
}
