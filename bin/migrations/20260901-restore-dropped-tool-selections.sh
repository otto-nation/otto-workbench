#!/usr/bin/env bash
# Migration: restore sub-tool selections that an interrupted install emptied.
# state_load_selections used to clear the saved list to force a fresh menu, so an
# install that ended before recording an answer — a declined menu, a step that
# failed under `set -e`, a Ctrl-C — left the list short or empty for good, and
# sync then skipped every tool missing from it without printing anything.
#
# Detection reads the filesystem, and deselecting a tool never removed its files,
# so this cannot tell a tool the bug dropped from one the operator chose to drop.
# It therefore names every tool it adds back and how to undo it, rather than
# repairing silently — a wrong guess here re-syncs a tool, which is recoverable,
# while leaving it out keeps a machine quietly out of sync.

# _restored_tools COMPONENT BEFORE — prints the tools COMPONENT gained relative
# to the newline-separated BEFORE list.
_restored_tools() {
  local component="$1" before="$2" tool
  while IFS= read -r tool; do
    [[ -z "$tool" ]] && continue
    if [[ $'\n'"$before"$'\n' != *$'\n'"$tool"$'\n'* ]]; then printf '%s/%s\n' "$component" "$tool"; fi
  done < <(state_get_list "${component}.tools")
}

migration_20260901_restore_dropped_tool_selections() {
  # Nothing recorded yet — the initial-state migrations own that case, and they
  # run first because migrations are applied in filename order.
  [[ -f "$INSTALL_YML_FILE" ]] || return "$MIGRATION_DEFERRED"

  local component entry _before_ai _before_editors _before_terminals
  _before_ai=$(state_get_list "ai.tools")
  _before_editors=$(state_get_list "editors.tools")
  _before_terminals=$(state_get_list "terminals.tools")

  # Records only what it finds, and state_record merges into the existing list,
  # so a file that never lost anything comes back unchanged.
  state_detect_installed

  local restored=()
  for component in ai editors terminals; do
    local _before_var="_before_${component}"
    while IFS= read -r entry; do
      [[ -n "$entry" ]] && restored+=("$entry")
    done < <(_restored_tools "$component" "${!_before_var}")
  done

  [[ ${#restored[@]} -eq 0 ]] && return "$MIGRATION_NOOP"

  success "Restored installed tools dropped from install.yml: ${restored[*]}"
  info "Set WORKBENCH_INTERACTIVE=1 and re-run that component's setup.sh to change the selection"
}
