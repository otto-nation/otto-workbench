#!/usr/bin/env bash
# Hand-authored workbench settings, read from YAML.
#
# Two scopes, project first: <repo>/.workbench.yml then $WORKBENCH_CONFIG_FILE.
# ai/lib/workbench_config.py is the typed owner of the same files and the source
# the committed config.schema.json is generated from; this is the reader for
# bash callers, which want single keys rather than the whole document.
#
# Usage (from scripts that already source lib/ui.sh):
#   wb_config_get "reuse.level"           # value, or nothing
#   wb_config_get "reuse.level" "full"    # value, or the given default

# Guard: constants must be loaded (provides WORKBENCH_CONFIG_FILE)
if [[ -z "${WORKBENCH_CONFIG_FILE:-}" ]]; then
  echo "ERROR: lib/config.sh requires WORKBENCH_CONFIG_FILE (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

WORKBENCH_PROJECT_CONFIG_NAME=".workbench.yml"

# _wb_config_project_file — the project config for $PWD, or nothing.
# Outside a git repo there is no project scope, which is not an error.
_wb_config_project_file() {
  local toplevel
  toplevel="$(git rev-parse --show-toplevel 2>/dev/null)" || return 0
  local candidate="$toplevel/$WORKBENCH_PROJECT_CONFIG_NAME"
  if [[ -f "$candidate" ]]; then printf '%s' "$candidate"; fi
}

# _wb_config_read FILE KEY — one key from one file, or nothing.
# A malformed file reads as absent: a bash caller wants its default, not a yq
# parse error on stdout. The typed loader is where a bad file is reported.
_wb_config_read() {
  local file="$1" key="$2" value
  [[ -f "$file" ]] || return 0
  value="$(yq -r ".$key // \"\"" "$file" 2>/dev/null)" || return 0
  if [[ -n "$value" && "$value" != "null" ]]; then printf '%s' "$value"; fi
}

# wb_config_get KEY [DEFAULT] — a dotted config key, project scope first.
wb_config_get() {
  local key="$1" fallback="${2:-}" value project

  project="$(_wb_config_project_file)"
  if [[ -n "$project" ]]; then
    value="$(_wb_config_read "$project" "$key")"
    if [[ -n "$value" ]]; then echo "$value"; return 0; fi
  fi

  value="$(_wb_config_read "$WORKBENCH_CONFIG_FILE" "$key")"
  if [[ -n "$value" ]]; then echo "$value"; return 0; fi

  if [[ -n "$fallback" ]]; then echo "$fallback"; fi
  return 0
}
