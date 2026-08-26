#!/usr/bin/env bash
# Hand-authored settings, read from YAML — one file per scope, this checkout first.
#
# ```bash
# wb_config_get "reuse.level"           # value, or nothing
# wb_config_get "reuse.level" "full"    # value, or the given default
# ```
#
# The read is not performed here. `wb_config_get` shells out to `otto-workbench
# config get`, which is [`lib/config_cli.py`](../lib/config_cli.py) over
# [`ai/lib/workbench_config.py`](../ai/lib/workbench_config.py) — the typed
# owner of these files, and the only thing that knows all three scopes. Bash
# used to carry two partial readers of its own and they disagreed with the
# loader about the same repo in the same session: the machine profile called a
# tracker recorded above a bare repo's worktrees `unset` while the SessionStart
# line named it. A bash caller that needs a config value asks that command; a
# second reader here is the bug, not a shortcut.
#
# A file nothing can parse still reads as the built-in default, which is what
# `load_config_or_default` gives every other reader on the machine — so a
# caller's own fallback still applies and a `yq` parse error never lands on
# stdout. A key the config surface does not define is refused instead, loudly:
# that is a caller asking a question with no answer.
#
# Both filenames, the schema URL and the modeline are declared once in
# [`constants.sh`](#constantssh) — as `WORKBENCH_CONFIG_FILE`,
# `WORKBENCH_PROJECT_CONFIG_NAME`, `WORKBENCH_CONFIG_SCHEMA_URL` and
# `WORKBENCH_CONFIG_HEADER` — and `config.sh` holds functions only. The typed
# owner spells those same names a second time for Python, and
# `tests/config.bats` fails when a pair drifts. The scope and key tables below
# are generated from the dataclass by `bin/local/generate-config-schema`,
# alongside [`config.schema.json`](../config.schema.json);
# `tests/test_workbench_config.py` fails if the committed schema goes stale.
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
# Writes go through `otto-workbench config set KEY VALUE` (`--project` for the
# repo's own committed file, `--container` for the one above its worktrees),
# which is
# [`lib/config_cli.py`](../lib/config_cli.py) over `set_value`. It refuses a key
# neither this checkout nor the *installed* workbench reads: a checkout can be
# weeks behind `main` and still write the file every repo on the machine shares,
# and `serde` drops an unknown key on read, so a value recorded under a name the
# config has moved off is gone with nothing said at either end. Both surfaces are
# consulted because only the installed one can catch a stale writer using a key
# that is still valid where it is standing. Hand-editing stays supported — that
# is what the modeline is for — but nothing checks the key.
#
# `otto-workbench config status` reports the read side: every scope in
# precedence order with its path and whether the file is there, every key with
# the value it resolved to and the file that supplied it, and any key a file
# holds that the surface does not have. Nothing else answers that — the loader
# merges the scopes and returns the result, so an inherited value and a local
# one are indistinguishable afterwards, and a key written under a name nothing
# reads is dropped in silence by both the loader and the reader waiting on it.
#
# Six layers decide an agent value, highest first:
#
# | # | Layer | Example |
# |---|-------|---------|
# | 1 | Explicit flag | `--model opus`, `--effort high` |
# | 2 | Phase env var | `WORKBENCH_AI_SCOUT_MODEL` |
# | 3 | Global env var | `WORKBENCH_AI_MODEL` |
# | 4 | Project config | `<worktree>/.workbench.yml` |
# | 5 | Container config | `<container>/.workbench.yml` |
# | 6 | Global config | `config.yml` |
#
# Within one file an `agent.phases.<phase>` entry outranks the `agent.*` section
# it sits under. Layers 4 through 6 deep-merge, so a project file that sets one
# phase keeps every sibling the layers below it set.
#
# Layer 5 exists only in the bare-repo worktree layout, where every checkout is
# a peer of the bare `.git` inside a container directory — the same directory
# `otto-workbench permissions mirror` writes a repo's grants into. It is the
# scope for an answer that belongs to the repo but cannot be committed to it: a
# worktree file has to be copied into every checkout, is missing from whichever
# one `wt switch -c` just cut, and is deleted along with the worktree by `wt
# remove`. Ordered by specificity — this checkout, then this repo, then this
# machine. A plain clone has no container and keeps exactly two layers.
#
# `wb_config_get` below reads all three, because it does not read them itself —
# it asks the loader, which is the only thing that knows layer 5 is there.
# `otto-workbench config status` is where all three are visible.
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

# wb_config_split_record RECORD SCOPE_OUT VALUE_OUT DIR_OUT — the three fields
# of one `otto-workbench config get` record, assigned to the named variables.
#
# Split by parameter expansion rather than by `IFS=$'\t' read`, which cannot do
# it. A tab is an IFS whitespace character, so bash folds a run of them into one
# delimiter and the empty field between them disappears — and an empty field is
# what a key no scope sets looks like, which is the commonest record there is.
# `read` hands back the directory as the value and leaves the directory empty,
# with nothing said. Splitting from both ends keeps every field where the
# resolver put it, and leaves the remainder of the line in DIR_OUT so a path
# holding a tab can only ever confuse itself.
wb_config_split_record() {
  local __record="$1" __rest
  local -n __scope="$2" __value="$3" __dir="$4"
  __scope="${__record%%$'\t'*}"
  __rest="${__record#*$'\t'}"
  __value="${__rest%%$'\t'*}"
  __dir="${__rest#*$'\t'}"
}

# wb_config_get KEY [DEFAULT] — a dotted config key resolved for the repo the
# caller is standing in, or DEFAULT when no scope sets it. Returns 1, printing
# why, when KEY is not one the config surface defines.
#
# The scopes are resolved by `otto-workbench config get`, whose record format is
# documented in `lib/config_cli.py`. Only the value is taken here: a caller of
# this function is asking what the setting is, and the scope that answered is a
# question for `config status` or for a caller reading the records itself.
#
# A refused key is the one failure worth reporting. It cannot be a bad file — an
# unreadable scope resolves to the built-in default and DEFAULT still applies —
# so it is the caller naming a key nothing reads, which no fallback should
# paper over.
#
# One call is one python interpreter, which is the price of reading the same
# files everything else reads. That is nothing against a `sync` step, and the
# wrong shape for a loop: `otto-workbench config get KEY DIR ...` resolves a
# whole list in one process and prints a record per directory, which is what the
# machine profile's registry table does. Reach for that rather than for this
# function called once per item.
wb_config_get() {
  local key="$1" fallback="${2:-}" record
  # shellcheck disable=SC2034  # both are written through namerefs below
  local scope value dir

  record="$(python3 "$WORKBENCH_DIR/lib/config_cli.py" get "$key")" || return 1

  wb_config_split_record "$record" scope value dir
  if [[ -n "$value" ]]; then echo "$value"; return 0; fi

  if [[ -n "$fallback" ]]; then echo "$fallback"; fi
  return 0
}
