#!/usr/bin/env bash
# Fixture writers shared by the validate_skills_* suites: a SKILL.md and the validator run.

# Helper: create a valid SKILL.md with optional lifecycle fields
_make_skill() {
  local name="$1"
  local cadence="${2:-}"
  local scope="${3:-}"
  local trigger="${4:-Use when testing}"
  local dir="$FAKE_WORKBENCH/ai/skills/$name"
  mkdir -p "$dir"

  {
    echo "---"
    echo "name: $name"
    echo "description: \"Test skill description.\""
    echo "source: otto-workbench/ai/skills/$name/SKILL.md"
    echo "invocation: \"/$name\""
    echo "trigger: \"$trigger\""
    [[ -n "$cadence" ]] && echo "lifecycle_cadence: \"$cadence\""
    [[ -n "$scope" ]] && echo "lifecycle_scope: $scope"
    echo "---"
    echo ""
    echo "# $name"
  } > "$dir/SKILL.md"
}

_run_validate() {
  WORKBENCH_DIR="$FAKE_WORKBENCH" NO_COLOR=1 run "$VALIDATE_SKILLS" "$@"
}
