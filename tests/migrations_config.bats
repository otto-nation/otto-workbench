#!/usr/bin/env bats
# Tests for the config migrations — unification, the issue-tracker key lift and renames, the agent section, jira_url.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  load 'migrations_helper'
  common_setup
  _migrations_fake_workbench

  # Source the real component discovery and migrations libraries with our fake paths
  cp "$REPO_ROOT/lib/components.sh" "$FAKE_ROOT/lib/components.sh"
  cp "$REPO_ROOT/lib/migrations.sh" "$FAKE_ROOT/lib/migrations.sh"
  cp "$REPO_ROOT/lib/projects.sh" "$FAKE_ROOT/lib/projects.sh"
  cp "$REPO_ROOT/lib/gitenv.sh" "$FAKE_ROOT/lib/gitenv.sh"
  cp "$REPO_ROOT/lib/git_layout.sh" "$FAKE_ROOT/lib/git_layout.sh"
  # _project_rewrite reads a file's mode through portable.sh, and
  # record_project_repo_ids is the first thing on the sync path to call it.
  cp "$REPO_ROOT/lib/portable.sh" "$FAKE_ROOT/lib/portable.sh"
}

teardown() {
  common_teardown
}

# ─── Config unification ──────────────────────────────────────────────────────

unify_in_fake() {
  (
    export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
    . "$FAKE_ROOT/lib/ui.sh"
    # The real constants and config.sh, not the stubs: the migration writes to
    # WORKBENCH_CONFIG_FILE and seeds it through wb_config_ensure_file, which is
    # where the schema modeline comes from. The real ui.sh sources both the same
    # way, and constants.sh builds the file path from the root exported above.
    . "$REPO_ROOT/lib/constants.sh"
    . "$REPO_ROOT/lib/config.sh"
    # For MIGRATION_NOOP and MIGRATION_DEFERRED, which the migration body
    # returns by name. The subshell keeps them from reaching the assertions
    # outside, which spell the numbers out.
    . "$REPO_ROOT/lib/migrations.sh"
    . "$REPO_ROOT/bin/migrations/20260814-unify-workbench-config.sh"
    migration_20260814_unify_workbench_config
  )
}

@test "unification is a no-op when no legacy file exists" {
  # NOOP, not deferred: nothing writes the three legacy files any more, so a
  # machine without them will not grow them, and the adoption that can re-seed
  # them forgets this migration's state line outright.
  run unify_in_fake
  [ "$status" -eq 3 ]
  [ ! -f "$FAKE_CONFIG/config.yml" ]
}

@test "unification folds every legacy file into config.yml" {
  mkdir -p "$FAKE_CONFIG"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"
  echo "lite" > "$FAKE_CONFIG/reuse-default"
  printf 'issue_tracker:\n  provider: github\n  team: ENG\n' > "$FAKE_CONFIG/review.yml"

  run unify_in_fake
  [ "$status" -eq 0 ]

  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
  [ "$(yq -r '.reuse.default' "$FAKE_CONFIG/config.yml")" = "lite" ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
  [ "$(yq -r '.issue_tracker.team' "$FAKE_CONFIG/config.yml")" = "ENG" ]

  [ ! -f "$FAKE_CONFIG/reuse-level" ]
  [ -f "$FAKE_CONFIG/reuse-level.migrated" ]
  [ -f "$FAKE_CONFIG/reuse-default.migrated" ]
  [ -f "$FAKE_CONFIG/review.yml.migrated" ]
}

@test "unification folds a partial set of legacy files" {
  mkdir -p "$FAKE_CONFIG"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"

  run unify_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
  [ "$(yq -r '.reuse.default // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "unification leaves a key config.yml already holds" {
  mkdir -p "$FAKE_CONFIG"
  printf 'reuse:\n  level: lite\n' > "$FAKE_CONFIG/config.yml"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"

  run unify_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "lite" ]
  [ -f "$FAKE_CONFIG/reuse-level.migrated" ]
}

@test "unification still folds keys an existing config.yml lacks" {
  mkdir -p "$FAKE_CONFIG"
  printf 'reuse:\n  level: lite\n' > "$FAKE_CONFIG/config.yml"
  echo "full" > "$FAKE_CONFIG/reuse-default"
  printf 'issue_tracker:\n  provider: jira\n' > "$FAKE_CONFIG/review.yml"

  run unify_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "lite" ]
  [ "$(yq -r '.reuse.default' "$FAKE_CONFIG/config.yml")" = "full" ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "jira" ]
}

@test "unification renames a review.yml with nothing to carry" {
  mkdir -p "$FAKE_CONFIG"
  printf 'unrelated: true\n' > "$FAKE_CONFIG/review.yml"

  run unify_in_fake
  [ "$status" -eq 0 ]
  [ -f "$FAKE_CONFIG/review.yml.migrated" ]
  [ "$(yq -r '.issue_tracker // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "unification seeds a new config.yml with the schema modeline" {
  mkdir -p "$FAKE_CONFIG"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"

  run unify_in_fake
  [ "$status" -eq 0 ]

  run head -1 "$FAKE_CONFIG/config.yml"
  [[ "$output" == "# yaml-language-server: \$schema="* ]]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
}

@test "unification leaves a config.yml the user already wrote unseeded" {
  mkdir -p "$FAKE_CONFIG"
  printf 'reuse:\n  default: lite\n' > "$FAKE_CONFIG/config.yml"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"

  run unify_in_fake
  [ "$status" -eq 0 ]
  run head -1 "$FAKE_CONFIG/config.yml"
  [[ "$output" != *"yaml-language-server"* ]]
}

@test "unification re-run after a fold is a no-op" {
  mkdir -p "$FAKE_CONFIG"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"

  run unify_in_fake
  [ "$status" -eq 0 ]
  run unify_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
}

@test "a mid-fold yq failure surfaces non-zero and leaves the source un-renamed" {
  mkdir -p "$FAKE_CONFIG"
  echo "ultra" > "$FAKE_CONFIG/reuse-level"
  # Malformed YAML makes the fold's `yq` read fail rather than parse.
  printf 'issue_tracker: [unclosed\n' > "$FAKE_CONFIG/review.yml"

  run unify_in_fake
  [ "$status" -eq 1 ]
  [[ "$output" == *"Could not fold every setting into config.yml"* ]]

  # The failing fold's source is left in place, not renamed.
  [ -f "$FAKE_CONFIG/review.yml" ]
  [ ! -f "$FAKE_CONFIG/review.yml.migrated" ]
  # A fold that succeeded before the failure still carried its value over.
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
  [ -f "$FAKE_CONFIG/reuse-level.migrated" ]
}

# ─── Issue tracker key lift ──────────────────────────────────────────────────

lift_in_fake() {
  (
    export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
    . "$FAKE_ROOT/lib/ui.sh"
    . "$REPO_ROOT/lib/constants.sh"
    # For the status names the migration body returns, as in unify_in_fake above.
    . "$REPO_ROOT/lib/migrations.sh"
    . "$REPO_ROOT/bin/migrations/20260824-lift-issue-tracker-key.sh"
    migration_20260824_lift_issue_tracker_key
  )
}

@test "lift defers while there is no config.yml" {
  # The bug this migration was re-dated for: the 20260819 version returned 0
  # here, was recorded, and had nothing left to lift when a session wrote the
  # legacy shape into a new config.yml half an hour later.
  run lift_in_fake
  [ "$status" -eq 4 ]
  [ ! -f "$FAKE_CONFIG/config.yml" ]
}

@test "lift is a no-op when the key is already top-level" {
  # Recorded rather than deferred: the file is here and holds no legacy key, and
  # nothing writes .review.issue_tracker any more.
  mkdir -p "$FAKE_CONFIG"
  printf 'issue_tracker:\n  provider: github\n' > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
}

@test "lift picks up a config.yml written after an earlier sync deferred it" {
  # A deferred migration is not recorded, so the framework asks again — and the
  # legacy shape a later session wrote is lifted on that pass.
  run lift_in_fake
  [ "$status" -eq 4 ]

  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  issue_tracker:\n    provider: github\n' > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
}

@test "lift moves the whole legacy mapping to the top level" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  issue_tracker:\n    provider: github\n    team: ENG\n' \
    > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
  [ "$(yq -r '.issue_tracker.team' "$FAKE_CONFIG/config.yml")" = "ENG" ]
  [ "$(yq -r '.review.issue_tracker // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "lift drops a review section it emptied" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  issue_tracker:\n    provider: jira\n' > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "lift keeps a review section holding other settings" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  effort: high\n  issue_tracker:\n    provider: jira\n' \
    > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.review.effort' "$FAKE_CONFIG/config.yml")" = "high" ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "jira" ]
}

@test "lift keeps a top-level value already written against the new schema" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  issue_tracker:\n    provider: jira\nissue_tracker:\n  provider: github\n' \
    > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "lift preserves the schema modeline and hand-written comments" {
  mkdir -p "$FAKE_CONFIG"
  printf '# yaml-language-server: $schema=https://example/config.schema.json\n# we file on GitHub\nreview:\n  issue_tracker:\n    provider: github\n' \
    > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  run head -1 "$FAKE_CONFIG/config.yml"
  [[ "$output" == "# yaml-language-server: \$schema="* ]]
  grep -q "# we file on GitHub" "$FAKE_CONFIG/config.yml"
}

@test "lift re-run after a move is a no-op" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  issue_tracker:\n    provider: github\n' > "$FAKE_CONFIG/config.yml"

  run lift_in_fake
  [ "$status" -eq 0 ]
  run lift_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.issue_tracker.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
}

# ─── Agent config section ────────────────────────────────────────────────────

agent_section_in_fake() {
  (
    export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
    . "$FAKE_ROOT/lib/ui.sh"
    . "$REPO_ROOT/lib/constants.sh"
    # For the status names the migration body returns, as in lift_in_fake above.
    . "$REPO_ROOT/lib/migrations.sh"
    . "$REPO_ROOT/bin/migrations/20260824-agent-config-section.sh"
    migration_20260824_agent_config_section
  )
}

@test "agent section defers while there is no config.yml" {
  # Deferred, not recorded: a session that writes the legacy shape into a new
  # config.yml after this sync still has the move waiting for it.
  run agent_section_in_fake
  [ "$status" -eq 4 ]
  [ ! -f "$FAKE_CONFIG/config.yml" ]
}

@test "agent section is a no-op when review holds none of the keys" {
  # Recorded rather than deferred: the file is here and holds no legacy key, and
  # nothing writes review.model any more.
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  effort: high\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.review.effort' "$FAKE_CONFIG/config.yml")" = "high" ]
}

@test "agent section picks up a config.yml written after a deferred sync" {
  run agent_section_in_fake
  [ "$status" -eq 4 ]

  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  model: opus\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.agent.model' "$FAKE_CONFIG/config.yml")" = "opus" ]
}

@test "agent section moves every sizing key across" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  model: opus\n  thinking: high\n  provider: pi\n  effort: high\n' \
    > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.agent.model' "$FAKE_CONFIG/config.yml")" = "opus" ]
  [ "$(yq -r '.agent.thinking' "$FAKE_CONFIG/config.yml")" = "high" ]
  [ "$(yq -r '.agent.provider' "$FAKE_CONFIG/config.yml")" = "pi" ]
  [ "$(yq -r '.review.model // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
  [ "$(yq -r '.review.thinking // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
  [ "$(yq -r '.review.provider // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "agent section moves the phases mapping whole" {
  # phases is the one nested value: every per-phase entry has to survive the
  # move, not just the key naming them.
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  phases:\n    scout:\n      model: haiku\n    group:\n      thinking: low\n' \
    > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.agent.phases.scout.model' "$FAKE_CONFIG/config.yml")" = "haiku" ]
  [ "$(yq -r '.agent.phases.group.thinking' "$FAKE_CONFIG/config.yml")" = "low" ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "agent section keeps effort behind in review" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  effort: low\n  model: sonnet\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.review.effort' "$FAKE_CONFIG/config.yml")" = "low" ]
  [ "$(yq -r '.agent.model' "$FAKE_CONFIG/config.yml")" = "sonnet" ]
}

@test "agent section drops a review section it emptied" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  model: opus\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "agent section keeps a value already written against the new schema" {
  # A machine that hand-wrote agent.model before the sync reached it: the value
  # under the key the loader now reads is the one the operator chose last.
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  model: opus\nagent:\n  model: sonnet\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.agent.model' "$FAKE_CONFIG/config.yml")" = "sonnet" ]
  [ "$(yq -r '.review // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "agent section preserves the schema modeline and hand-written comments" {
  mkdir -p "$FAKE_CONFIG"
  printf '# yaml-language-server: $schema=https://example/config.schema.json\n# opus reviews better\nreview:\n  model: opus\n' \
    > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  run head -1 "$FAKE_CONFIG/config.yml"
  [[ "$output" == "# yaml-language-server: \$schema="* ]]
  grep -q "# opus reviews better" "$FAKE_CONFIG/config.yml"
}

@test "agent section re-run after a move is a no-op" {
  mkdir -p "$FAKE_CONFIG"
  printf 'review:\n  model: opus\n  effort: high\n' > "$FAKE_CONFIG/config.yml"

  run agent_section_in_fake
  [ "$status" -eq 0 ]
  run agent_section_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.agent.model' "$FAKE_CONFIG/config.yml")" = "opus" ]
  [ "$(yq -r '.review.effort' "$FAKE_CONFIG/config.yml")" = "high" ]
}

# ─── Issue tracker section rename ────────────────────────────────────────────

rename_key_in_fake() {
  (
    export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
    . "$FAKE_ROOT/lib/ui.sh"
    . "$REPO_ROOT/lib/constants.sh"
    # For the status names the migration body returns, as in lift_in_fake above.
    . "$REPO_ROOT/lib/migrations.sh"
    . "$REPO_ROOT/bin/migrations/20260909-rename-issue-tracker-key.sh"
    migration_20260909_rename_issue_tracker_key
  )
}

@test "rename defers while there is no config.yml" {
  run rename_key_in_fake
  [ "$status" -eq 4 ]
  [ ! -f "$FAKE_CONFIG/config.yml" ]
}

@test "rename is a no-op when the section is already issues" {
  mkdir -p "$FAKE_CONFIG"
  printf 'issues:\n  provider: github\n' > "$FAKE_CONFIG/config.yml"

  run rename_key_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.issues.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
}

@test "rename picks up a config.yml written after an earlier sync deferred it" {
  run rename_key_in_fake
  [ "$status" -eq 4 ]

  mkdir -p "$FAKE_CONFIG"
  printf 'issue_tracker:\n  provider: github\n' > "$FAKE_CONFIG/config.yml"

  run rename_key_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issues.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
}

@test "rename moves the whole mapping across" {
  mkdir -p "$FAKE_CONFIG"
  printf 'issue_tracker:\n  provider: github\n  team: ENG\n  labels:\n    - follow-up\n' \
    > "$FAKE_CONFIG/config.yml"

  run rename_key_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issues.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
  [ "$(yq -r '.issues.team' "$FAKE_CONFIG/config.yml")" = "ENG" ]
  [ "$(yq -r '.issues.labels[0]' "$FAKE_CONFIG/config.yml")" = "follow-up" ]
  [ "$(yq -r '.issue_tracker // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "rename keeps a value already written against the new schema" {
  mkdir -p "$FAKE_CONFIG"
  printf 'issue_tracker:\n  provider: jira\nissues:\n  provider: github\n' \
    > "$FAKE_CONFIG/config.yml"

  run rename_key_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issues.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
  [ "$(yq -r '.issue_tracker // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "rename leaves the other sections alone" {
  mkdir -p "$FAKE_CONFIG"
  printf 'reuse:\n  level: ultra\nissue_tracker:\n  provider: jira\n' \
    > "$FAKE_CONFIG/config.yml"

  run rename_key_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
  [ "$(yq -r '.issues.provider' "$FAKE_CONFIG/config.yml")" = "jira" ]
}

@test "rename preserves the schema modeline and hand-written comments" {
  mkdir -p "$FAKE_CONFIG"
  printf '# yaml-language-server: $schema=https://example/config.schema.json\n# we file on GitHub\nissue_tracker:\n  provider: github\n' \
    > "$FAKE_CONFIG/config.yml"

  run rename_key_in_fake
  [ "$status" -eq 0 ]
  run head -1 "$FAKE_CONFIG/config.yml"
  [[ "$output" == "# yaml-language-server: \$schema="* ]]
  grep -q "# we file on GitHub" "$FAKE_CONFIG/config.yml"
}

@test "rename re-run after a move is a no-op" {
  mkdir -p "$FAKE_CONFIG"
  printf 'issue_tracker:\n  provider: github\n' > "$FAKE_CONFIG/config.yml"

  run rename_key_in_fake
  [ "$status" -eq 0 ]
  run rename_key_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.issues.provider' "$FAKE_CONFIG/config.yml")" = "github" ]
}

# ─── Issue tracker rename: the repo file is reported, not rewritten ──────────

warn_project_in_fake() {
  (
    export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
    . "$FAKE_ROOT/lib/ui.sh"
    . "$REPO_ROOT/lib/constants.sh"
    . "$REPO_ROOT/lib/migrations.sh"
    . "$REPO_ROOT/bin/migrations/20260909-warn-issue-tracker-project.sh"
    migration_20260909_warn_issue_tracker_project "$1"
  )
}

@test "a repo config holding the old key is reported and left untouched" {
  mkdir -p "$TMPDIR/repo"
  printf 'issue_tracker:\n  provider: github\n' > "$TMPDIR/repo/.workbench.yml"

  run warn_project_in_fake "$TMPDIR/repo"
  [ "$status" -eq 0 ]
  [[ "$output" == *"still names issue_tracker"* ]]
  # Untouched is the point — the fix belongs in a commit its owner makes.
  [ "$(yq -r '.issue_tracker.provider' "$TMPDIR/repo/.workbench.yml")" = "github" ]
  [ "$(yq -r '.issues // "absent"' "$TMPDIR/repo/.workbench.yml")" = "absent" ]
}

@test "a repo config already on the new key says nothing" {
  mkdir -p "$TMPDIR/repo"
  printf 'issues:\n  provider: github\n' > "$TMPDIR/repo/.workbench.yml"

  run warn_project_in_fake "$TMPDIR/repo"
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

@test "a repo with no config at all says nothing" {
  mkdir -p "$TMPDIR/repo"

  run warn_project_in_fake "$TMPDIR/repo"
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

# ─── issues.jira_url renamed to issues.base_url ──────────────────────────────

rename_jira_url_in_fake() {
  (
    export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
    . "$FAKE_ROOT/lib/ui.sh"
    . "$REPO_ROOT/lib/constants.sh"
    # For the status names the migration body returns, as in lift_in_fake above.
    . "$REPO_ROOT/lib/migrations.sh"
    . "$REPO_ROOT/bin/migrations/20260922-rename-issues-jira-url.sh"
    migration_20260922_rename_issues_jira_url
  )
}

@test "jira_url rename defers while there is no config.yml" {
  run rename_jira_url_in_fake
  [ "$status" -eq 4 ]
  [ ! -f "$FAKE_CONFIG/config.yml" ]
}

@test "jira_url rename is a no-op when the key is already base_url" {
  mkdir -p "$FAKE_CONFIG"
  printf 'issues:\n  provider: jira\n  base_url: https://j.example\n' \
    > "$FAKE_CONFIG/config.yml"

  run rename_jira_url_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.issues.base_url' "$FAKE_CONFIG/config.yml")" = "https://j.example" ]
}

@test "jira_url rename carries the host across unchanged" {
  mkdir -p "$FAKE_CONFIG"
  printf 'issues:\n  provider: jira\n  jira_url: https://j.example\n' \
    > "$FAKE_CONFIG/config.yml"

  run rename_jira_url_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issues.base_url' "$FAKE_CONFIG/config.yml")" = "https://j.example" ]
  [ "$(yq -r '.issues.jira_url // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
  [ "$(yq -r '.issues.provider' "$FAKE_CONFIG/config.yml")" = "jira" ]
}

@test "jira_url rename keeps a value already written against the new schema" {
  mkdir -p "$FAKE_CONFIG"
  printf 'issues:\n  jira_url: https://old.example\n  base_url: https://new.example\n' \
    > "$FAKE_CONFIG/config.yml"

  run rename_jira_url_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.issues.base_url' "$FAKE_CONFIG/config.yml")" = "https://new.example" ]
  [ "$(yq -r '.issues.jira_url // "absent"' "$FAKE_CONFIG/config.yml")" = "absent" ]
}

@test "jira_url rename leaves the other issues keys alone" {
  mkdir -p "$FAKE_CONFIG"
  printf 'reuse:\n  level: ultra\nissues:\n  provider: jira\n  team: ENG\n  jira_url: https://j.example\n' \
    > "$FAKE_CONFIG/config.yml"

  run rename_jira_url_in_fake
  [ "$status" -eq 0 ]
  [ "$(yq -r '.reuse.level' "$FAKE_CONFIG/config.yml")" = "ultra" ]
  [ "$(yq -r '.issues.team' "$FAKE_CONFIG/config.yml")" = "ENG" ]
}

@test "jira_url rename re-run after a move is a no-op" {
  mkdir -p "$FAKE_CONFIG"
  printf 'issues:\n  jira_url: https://j.example\n' > "$FAKE_CONFIG/config.yml"

  run rename_jira_url_in_fake
  [ "$status" -eq 0 ]
  run rename_jira_url_in_fake
  [ "$status" -eq 3 ]
  [ "$(yq -r '.issues.base_url' "$FAKE_CONFIG/config.yml")" = "https://j.example" ]
}

# ─── jira_url rename: the repo file is reported, not rewritten ───────────────

warn_jira_url_project_in_fake() {
  (
    export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
    . "$FAKE_ROOT/lib/ui.sh"
    . "$REPO_ROOT/lib/constants.sh"
    . "$REPO_ROOT/lib/migrations.sh"
    . "$REPO_ROOT/bin/migrations/20260922-warn-issues-jira-url-project.sh"
    migration_20260922_warn_issues_jira_url_project "$1"
  )
}

@test "a repo config holding jira_url is reported and left untouched" {
  mkdir -p "$TMPDIR/repo"
  printf 'issues:\n  provider: jira\n  jira_url: https://j.example\n' \
    > "$TMPDIR/repo/.workbench.yml"

  run warn_jira_url_project_in_fake "$TMPDIR/repo"
  [ "$status" -eq 0 ]
  [[ "$output" == *"still names issues.jira_url"* ]]
  # Untouched is the point — the fix belongs in a commit its owner makes.
  [ "$(yq -r '.issues.jira_url' "$TMPDIR/repo/.workbench.yml")" = "https://j.example" ]
  [ "$(yq -r '.issues.base_url // "absent"' "$TMPDIR/repo/.workbench.yml")" = "absent" ]
}

@test "a repo config already on base_url says nothing" {
  mkdir -p "$TMPDIR/repo"
  printf 'issues:\n  base_url: https://j.example\n' > "$TMPDIR/repo/.workbench.yml"

  run warn_jira_url_project_in_fake "$TMPDIR/repo"
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

@test "a repo with no config at all says nothing about jira_url" {
  mkdir -p "$TMPDIR/repo"

  run warn_jira_url_project_in_fake "$TMPDIR/repo"
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}
