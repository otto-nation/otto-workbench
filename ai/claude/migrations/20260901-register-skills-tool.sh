#!/usr/bin/env bash
# Skills stopped being installed by a Claude step and became an AI sub-tool of
# their own. sync_ai dispatches only to the sub-tools install.yml records under
# ai.tools, so a machine whose state predates the split would silently stop
# installing skills into either harness — the state list is what has to learn
# the new name.

migration_20260901_register_skills_tool() {
  local tool found=false any=false
  while IFS= read -r tool; do
    [[ -z "$tool" ]] && continue
    any=true
    [[ "$tool" == "skills" ]] && found=true
  done < <(state_get_list "ai.tools")

  # An empty list is a machine that has never chosen any AI tools. Seeding one
  # here would hand it a sub-tool it never selected and skip the ai/setup.sh
  # menu it is still owed, so the answer is left to that menu.
  if [[ "$any" == false || "$found" == true ]]; then
    return "$MIGRATION_NOOP"
  fi

  state_append_list "ai.tools" "skills"
  return 0
}
