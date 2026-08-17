#!/usr/bin/env bash
# Hand-authored workbench settings, read from YAML.
#
# Two scopes, project first: <repo>/$WORKBENCH_PROJECT_CONFIG_NAME then
# $WORKBENCH_CONFIG_FILE. Both names, the schema URL and the modeline are
# declared in lib/constants.sh, which is the one place bash spells them.
# ai/lib/workbench_config.py is the typed owner of the same files and the source
# the committed schema is generated from; this is the reader for bash callers,
# which want single keys rather than the whole document.
#
# Usage (from scripts that already source lib/ui.sh):
#   wb_config_get "reuse.level"           # value, or nothing
#   wb_config_get "reuse.level" "full"    # value, or the given default

# Guard: the config constants must be loaded. WORKBENCH_CONFIG_HEADER is the
# last of the block lib/constants.sh declares, so its presence proves the whole
# block is in scope — the file paths, the schema URL and the modeline alike.
if [[ -z "${WORKBENCH_CONFIG_HEADER:-}" ]]; then
  echo "ERROR: lib/config.sh requires the config constants (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

# wb_config_ensure_file [FILE] — create FILE holding just the modeline, when it
# does not already exist. `yq -i` needs a file to write into, and seeding it
# with the modeline rather than `{}` is what puts the schema in front of an
# editor. yq carries the comment through every later write, and both readers
# take a comment-only file as an empty mapping.
wb_config_ensure_file() {
  local file="${1:-$WORKBENCH_CONFIG_FILE}"
  if [[ -f "$file" ]]; then return 0; fi
  mkdir -p "$(dirname "$file")" || return 1
  printf '%s\n' "$WORKBENCH_CONFIG_HEADER" > "$file"
}

# _wb_config_project_file — the project config for $PWD, or nothing.
# Outside a git repo there is no project scope, which is not an error.
_wb_config_project_file() {
  local toplevel
  toplevel="$(git rev-parse --show-toplevel 2>/dev/null)" || return 0
  local candidate="$toplevel/$WORKBENCH_PROJECT_CONFIG_NAME"
  if [[ -f "$candidate" ]]; then printf '%s' "$candidate"; fi
}

# _wb_config_read FILE KEY — one key from one file, or nothing.
# A malformed file — and a missing yq — read as absent: a bash caller wants its
# default, not a yq parse error or a "command not found" on stdout. The typed
# loader (ai/lib/workbench_config.py) is where a bad file is reported, and every
# script that sources this already needs yq for lib/state.sh, so a missing
# binary surfaces there rather than here.
# A value of the literal string "null" is indistinguishable from an absent key.
# Nothing this reads is a string that spells null, and treating yq's null output
# as absent is what makes the fallback work.
_wb_config_read() {
  local file="$1" key="$2" value
  [[ -f "$file" ]] || return 0
  value="$(yq -r ".$key // \"\"" "$file" 2>/dev/null)" || return 0
  if [[ -n "$value" && "$value" != "null" ]]; then printf '%s' "$value"; fi
}

# wb_config_get KEY [DEFAULT] — a dotted config key, project scope first.
# KEY is interpolated into a yq expression, so it must be a literal path — the
# guard below rejects anything else rather than letting a built-up key become
# an expression.
wb_config_get() {
  local key="$1" fallback="${2:-}" value project

  if [[ ! "$key" =~ ^[A-Za-z0-9_][A-Za-z0-9_.]*$ ]]; then
    echo "ERROR: wb_config_get: invalid config key: $key" >&2
    return 1
  fi

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
