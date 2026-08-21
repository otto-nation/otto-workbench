#!/usr/bin/env bash
# Hand-authored settings, read from YAML — one file per scope, project first.
#
# ```bash
# wb_config_get "reuse.level"           # value, or nothing
# wb_config_get "reuse.level" "full"    # value, or the given default
# ```
#
# A malformed file reads as absent — a bash caller wants its default, not a `yq`
# parse error on stdout. Reporting a bad file is the typed loader's job. Both
# filenames, the schema URL and the modeline are declared once in
# [`constants.sh`](#constantssh) — as `WORKBENCH_CONFIG_FILE`,
# `WORKBENCH_PROJECT_CONFIG_NAME`, `WORKBENCH_CONFIG_SCHEMA_URL` and
# `WORKBENCH_CONFIG_HEADER` — and `config.sh` holds functions only.
#
# [`ai/lib/workbench_config.py`](../ai/lib/workbench_config.py) is the typed
# owner of the same two files: it deep-merges them into a `WorkbenchConfig` and
# rejects an unknown enum value or phase key rather than silently dropping it. It
# spells those same names a second time for Python, and `tests/config.bats` fails
# when a pair drifts. The scope and key tables below are generated from the
# dataclass by `bin/local/generate-config-schema`, alongside
# [`config.schema.json`](../config.schema.json); `tests/test_workbench_config.py`
# fails if the committed schema goes stale.
#
# <!-- include: bin/local/generate-config-schema --emit config-reference -->
#
# Both writers seed the modeline — `wb_config_ensure_file` in bash, `set_value`
# in Python — and `yq -i` carries it through every later write, so completion and
# enum validation work while the file is hand-edited. A `.workbench.yml` that the
# workbench creates for you — recording an answer such as
# `issue_tracker.provider` — is seeded the same way; paste it in yourself at the
# top of one you hand-author. A file that already exists is never seeded: the
# modeline is a courtesy on creation, not something sync re-imposes.
#
# Five layers decide a review value, highest first:
#
# | # | Layer | Example |
# |---|-------|---------|
# | 1 | Explicit flag | `--model opus`, `--effort high` |
# | 2 | Phase env var | `CLAUDE_REVIEW_SCOUT_MODEL` |
# | 3 | Global env var | `CLAUDE_REVIEW_MODEL` |
# | 4 | Project config | `.workbench.yml` |
# | 5 | Global config | `config.yml` |
#
# Within one file a `review.phases.<phase>` entry outranks the `review.*` section
# it sits under. Layers 4 and 5 deep-merge, so a project file that sets one phase
# keeps every global sibling.
#
# A repo still holding the legacy `.claude/review.yml` is converted to
# `.workbench.yml` the first time a review reads its issue tracker; the old file
# is left in place, since it is usually tracked in the consumer repo. The
# machine-wide files — `reuse-level`, `reuse-default`, `review.yml` — are folded
# into `config.yml` by `bin/migrations/20260814-unify-workbench-config.sh`.

# Guard: the config constants must be loaded. WORKBENCH_CONFIG_HEADER is the
# last of the block lib/constants.sh declares, so its presence proves the whole
# block is in scope — the file paths, the schema URL and the modeline alike.
if [[ -z "${WORKBENCH_CONFIG_HEADER:-}" ]]; then
  echo "ERROR: lib/config.sh requires the config constants (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

# wb_config_ensure_file [FILE] — create FILE holding just the modeline, when it
# does not already exist.
#
# `yq -i` needs a file to write into, and seeding it with the modeline rather
# than `{}` is what puts the schema in front of an editor. yq carries the
# comment through every later write, and both readers take a comment-only file
# as an empty mapping.
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

# wb_config_get KEY [DEFAULT] — a dotted config key, project scope first. KEY
# must be a literal string.
#
# The key is interpolated into a yq expression, so the guard below rejects
# anything else rather than letting a built-up key become an expression.
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
