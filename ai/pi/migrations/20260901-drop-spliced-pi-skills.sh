#!/usr/bin/env bash
# Removes the reviewer copy a retired Pi sync step spliced into ~/.pi/agent/skills/.
#
# The skill now installs to ~/.agents/skills/reviewer, which Pi also discovers.
# Left in place, both are found and Pi resolves the collision by keeping whichever
# it walked first — so a stale protocol body would win on some machines and not
# others, with only a warning to say so.
#
# No adoption-sensitive header: ~/.pi is Pi's own home, not a workbench root, so
# legacy-root adoption never re-seeds it.

migration_20260901_drop_spliced_pi_skills() {
  local skills_dir="$HOME/.pi/agent/skills"
  [[ -d "$skills_dir" ]] || return "$MIGRATION_NOOP"

  # Named entries only, and declared inside the function because a migration
  # file may run nothing at file scope: an operator's own skills live in this
  # same directory and are not ours to remove.
  local -a spliced=(reviewer)

  local name removed=0
  for name in "${spliced[@]}"; do
    if [[ -e "$skills_dir/$name" ]]; then
      rm -rf "${skills_dir:?}/$name"
      removed=1
    fi
  done

  [[ $removed -eq 1 ]] || return "$MIGRATION_NOOP"
  rmdir "$skills_dir" 2> /dev/null || true
  return 0
}
