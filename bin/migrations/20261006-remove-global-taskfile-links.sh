#!/usr/bin/env bash
# Migration: remove the ~/.config/task symlinks the retired task component made.
# step_task_symlinks linked Taskfile.global.yml as ~/.config/task/Taskfile.yml and
# the workbench lib/ as ~/.config/task/lib, for `task --global`. Both the wrapper
# and the global Taskfile are gone; nothing reads either link. Only symlinks are
# removed — taskfile.env, which holds the GitHub PATs, shares the directory and
# is never touched, nor is the directory itself. ~/.local/bin/task needs nothing
# here: bin's own sync prunes a symlink whose source was deleted.

migration_20261006_remove_global_taskfile_links() {
  # Defensive only: the framework always defines TASK_CONFIG_DIR, but
  # there is no `set -u` here, and an empty value would collapse the targets
  # to "/Taskfile.yml" and "/lib" on a script that deletes files. It fails
  # rather than answering NOOP: NOOP is recorded, which would retire the
  # migration without it ever having looked at the real directory.
  if [[ -z "$TASK_CONFIG_DIR" ]]; then
    warn "TASK_CONFIG_DIR is empty; not touching any task config links"
    return 1
  fi

  local removed=0 link
  for link in "$TASK_CONFIG_DIR/Taskfile.yml" "$TASK_CONFIG_DIR/lib"; do
    [[ -L "$link" ]] || continue
    if ! rm -f "$link"; then
      warn "Could not remove $link"
      return 1
    fi
    success "Removed $link"
    removed=1
  done
  if [[ "$removed" -eq 0 ]]; then
    return "$MIGRATION_NOOP"
  fi
  return 0
}
