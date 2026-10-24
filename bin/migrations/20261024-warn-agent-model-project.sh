#!/usr/bin/env bash
# checkout-scoped: the file it reports on is the one at this work tree's own
# root, so having reported it is a fact about the checkout.
# Migration: report a repo .workbench.yml still holding agent.model or
# agent.provider, rather than rewriting it.
#
# The two companion migrations move the machine-wide config.yml and the
# container file. This one changes nothing: a repo's .workbench.yml is tracked,
# and rewriting it would put an uncommitted diff in a checkout during a sync
# run for another reason. The fix belongs in a commit its owner makes.
#
# Reporting is not nothing. The loader ignores a key the config no longer
# has, so without this the repo's model choice would vanish with no error and
# its phases would run on whatever the machine's tiers name.

migration_20261024_warn_agent_model_project() {
  local work_tree="$1" file named
  file="$work_tree/$WORKBENCH_PROJECT_CONFIG_NAME"
  # NOOP rather than deferred: most repos have no .workbench.yml and never will.
  [[ -f "$file" ]] || return "$MIGRATION_NOOP"
  named="$(yq '[.agent.model, .agent.provider] | map(select(. != null)) | length' "$file")" || return 1
  [[ "$named" != "0" ]] || return "$MIGRATION_NOOP"
  warn "$file still names agent.model or agent.provider, which nothing reads — move it to models.<tier> as [provider/]model"
}
