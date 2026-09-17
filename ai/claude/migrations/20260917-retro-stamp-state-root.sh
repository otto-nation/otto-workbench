#!/usr/bin/env bash
set -e
# Migration: carry ~/.claude/.last-retro to the retro stamp under the state root.
#
# Machine-scoped, so no work tree is passed: the retro cooldown is one per
# machine rather than one per repo, because a retro is a single sweep over every
# registered repo's reviews rather than a per-repo pass.
#
# Moved and not dropped. Losing the timestamp is not one extra retro: the gate
# reads 0, fires on the next session exit, and retro-scan then treats the run as
# a first run — every merged PR the window allows fetched per registered repo,
# and every local review directory consumed and then deleted by
# retro-complete.sh. The stamp is small and the cost of it being absent is not.

migration_20260917_retro_stamp_state_root() {
  local old_stamp="$CLAUDE_DIR/.last-retro"

  # NOOP rather than DEFERRED: a machine with no stamp here has either never run
  # a retro or has already been carried, and neither becomes untrue later.
  # retro-complete.sh writes the new location now, so nothing recreates this.
  [[ -f "$old_stamp" ]] || return "$MIGRATION_NOOP"

  # The new stamp wins, and the old one goes. Both present means a retro has
  # completed since the move, so the old value is the staler of the two and
  # keeping it would only reopen a window that has already closed.
  if [[ -f "$RETRO_STAMP_FILE" ]]; then
    rm -f "$old_stamp"
    success "Dropped a superseded retro stamp at $old_stamp"
    return 0
  fi

  mkdir -p "$GATE_STAMPS_DIR"
  mv "$old_stamp" "$RETRO_STAMP_FILE"
  success "Carried the retro stamp to $RETRO_STAMP_FILE"
  return 0
}
