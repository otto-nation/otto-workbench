#!/usr/bin/env bash
# doc-group: registry
# Registry discovery, install-check gating, and env/auth iteration.
#
# The schema these functions read — the meta block, the tool entry fields, the
# `*.env.yml` shape, and the cross-validation modes — is documented once, in
# [Registries](registries.md#schema). `KNOWN_TOOL_FIELDS` and
# `KNOWN_COMMAND_FIELDS` below are what `validate-registries` rejects unknown
# keys against.
#
# Sourced directly by its consumers — `bin/local/generate-tool-context`,
# `bin/local/validate-registries`, `brew/summary.sh`, `summary.sh`, and
# `ai/claude/steps.sh`. Not in the `ui.sh` facade. It loads `roots.sh` itself when
# the caller has not already sourced `constants.sh`, since an
# `install_check_symlink` value may name a workbench root.

# install_check_symlink values may reference the workbench roots, so load them
# when the caller has not already sourced lib/constants.sh — tests source this
# module on its own.
if [[ -z "${WORKBENCH_STATE_DIR:-}" ]]; then
  # shellcheck source=./roots.sh
  . "$(dirname "${BASH_SOURCE[0]}")/roots.sh"
fi

# reg_load reports an unreadable registry with err(), and this module is sourced
# on its own by tests and by callers that have not loaded the facade — without
# this the failure path dies on "err: command not found" and the caller sees a
# bare non-zero instead of the file that could not be read.
if ! declare -F err >/dev/null; then
  # shellcheck source=./output.sh
  . "$(dirname "${BASH_SOURCE[0]}")/output.sh"
fi

# Known tool entry fields — used by validate-registries to reject unknown keys
# shellcheck disable=SC2034
KNOWN_TOOL_FIELDS="name description when_to_use permission visibility usage docs brew_name commands auth"
# Known command entry fields (within a tool's commands[] array)
# shellcheck disable=SC2034
KNOWN_COMMAND_FIELDS="name description scope when detail"
# Known env entry fields (within a registry's env[] array), same purpose
# shellcheck disable=SC2034
KNOWN_ENV_FIELDS="var target comment default setup_url prefix claude_env"

# is_installed NAME — returns 0 if NAME is found in PATH
is_installed() { command -v "$1" >/dev/null 2>&1; }

# ── The read cache ────────────────────────────────────────────────────────────
#
# Every value below is read through `reg_load` and the four accessors, not
# through a `yq` call per field. The reason is arithmetic: a `yq` fork costs
# about 11ms, and reading each field of each tool separately meant 2471 of them
# for one `validate-registries` run — 26 seconds, essentially all of it spent
# starting processes. One `yq` reads all 24 registries in 31ms.
#
# The stream `yq` emits is one record per node — `file \x01 path \x01 tag \x01
# value` — and deliberately never passes through `jq`. JSON has no `!!int` vs
# `!!float` vs `!!bool`, and a JSON round trip also rewrites the scalar's text:
# `1e3` comes back `1E+3`, `0x1F` comes back `31`, `00123` comes back `123`.
# `_check_permission_field` branches on the tag and prints it in its error, so
# both would be behaviour changes. yq's own tags and `tostring` preserve each
# exactly as the file spells it.
#
# `\x01` and `\x02` are the delimiters. A YAML scalar can hold either via a
# `"\x01"` escape, and in a *key* that would corrupt the record — the path field
# would absorb part of it and the tag slot would take the rest — so `reg_load`
# rejects a file whose keys hold one instead of mis-parsing it. Records are
# NUL-separated, so a multi-line block scalar survives intact.
#
# Sequence indices are prefixed with `#` in the key, so a sequence's element 0
# and a map's literal key `0` are different cache entries. yq renders both as
# the string "0" in a path, and without the marker `tools: {0: {name: x}}`
# would answer a `reg_get FILE tools 0 name` written for a list.

# The tag of each node, and the value of each node, keyed by "$file\x01$path".
# A map's value is its keys joined by \x02, in document order; a sequence's is
# its length.
declare -gA _REG_TAG=()
declare -gA _REG_VAL=()
# Which files are in the cache, so a second reg_load can skip them.
declare -gA _REG_LOADED=()

# readonly so a later assignment cannot desync the key format `_reg_key` builds
# from the one `_reg_stream` emits — the two would stop meeting and every lookup
# would miss, which reads as "absent" and is how a required-field check stops
# firing. Guarded because several callers source this module twice, and
# re-declaring a readonly is an error.
if [[ -z "${_REG_SEP:-}" ]]; then
  readonly _REG_SEP=$'\x01'
  readonly _REG_PSEP=$'\x02'
fi

# _reg_key FILE PATH_SEGMENT... — the cache key for one node.
#
# The join lives here alone so no call site spells the delimiter itself: a
# lookup that built its key by hand would silently miss every time the
# separator changed, and a miss reads as "absent", which is how a required-field
# check stops firing.
#
# An all-digit segment is a sequence index and is written `#N`, matching what
# `_reg_stream` emits for one. That is a deliberate asymmetry: the stream marks
# a segment by its parent's tag, while here every digit segment is marked, so
# these accessors cannot address a map whose key is a literal number. No caller
# needs to — a digit segment is only ever passed when iterating a sequence
# `reg_len` bounded — and the alternative is worse: a `tools: {0: {...}}` would
# answer a read written for a list, which is the shadowing this marker exists
# to prevent.
_reg_key() {
  local file="$1"; shift
  local path=""
  local seg
  for seg in "$@"; do
    [[ -n "$path" ]] && path+="$_REG_PSEP"
    [[ "$seg" =~ ^[0-9]+$ ]] && seg="#$seg"
    path+="$seg"
  done
  printf '%s' "$file$_REG_SEP$path"
}

# reg_load FILE... — read every node of each FILE into the cache.
#
# Idempotent: a file already loaded is skipped, so a function that loads the one
# file it was handed costs nothing when its caller already loaded the batch.
# Returns 1 when yq cannot parse a file, naming it. That is stricter than what
# it replaces, deliberately — a per-field `yq` on a malformed file returned an
# empty count, the entry loop ran zero times, and every check on that file
# passed by reading nothing.
reg_load() {
  local -a pending=()
  local file
  for file in "$@"; do
    [[ -n "${_REG_LOADED[$file]:-}" ]] && continue
    pending+=("$file")
  done
  (( ${#pending[@]} > 0 )) || return 0

  # Via a file, not a `$(...)`: the records are NUL-separated and command
  # substitution drops NUL bytes outright (with a warning on stderr), which
  # would run every record together into one. A file also keeps yq's exit
  # status readable, which a process substitution would not.
  local tmp
  tmp=$(mktemp "${TMPDIR:-/tmp}/reg-load.XXXXXX") || return 1
  if ! _reg_stream "${pending[@]}" > "$tmp"; then
    rm -f "$tmp"
    _reg_report_unparseable "${pending[@]}"
    return 1
  fi

  local record rec_file rec_path rec_tag rec_val mangled=""
  while IFS= read -r -d '' record; do
    # A record is four \x01-separated fields, and only the value may itself be
    # empty or multi-line — so the three prefixes are peeled off in order
    # rather than read into an IFS split, which would drop trailing empties.
    rec_file="${record%%"$_REG_SEP"*}"; record="${record#*"$_REG_SEP"}"
    rec_path="${record%%"$_REG_SEP"*}"; record="${record#*"$_REG_SEP"}"
    rec_tag="${record%%"$_REG_SEP"*}"
    rec_val="${record#*"$_REG_SEP"}"
    # A zero-byte file yields one record with an empty filename — there is no
    # node to key, and the file is recorded as seen below either way.
    [[ -n "$rec_file" ]] || continue
    # A tag slot holding something other than a YAML tag means a key held a raw
    # \x01: the split landed mid-key and every field after it is garbage. Noted
    # and reported after the loop, so the read finishes and the file is closed
    # before it is removed.
    if [[ "$rec_tag" != '!!'* ]]; then
      mangled="$rec_file"
      break
    fi
    _REG_TAG[$rec_file$_REG_SEP$rec_path]="$rec_tag"
    _REG_VAL[$rec_file$_REG_SEP$rec_path]="$rec_val"
  done < "$tmp"
  rm -f "$tmp"

  if [[ -n "$mangled" ]]; then
    err "reg_load: $mangled has a key containing a delimiter byte — remove the control character"
    return 1
  fi

  # Marked after parsing, not before: a file marked loaded on the way in would
  # stay marked after a failure above, and every later read of it would answer
  # empty rather than re-reading or failing.
  for file in "${pending[@]}"; do
    _REG_LOADED[$file]=1
  done
}

# _reg_report_unparseable FILE... — name the files in a failed batch that yq
# cannot read, one error each.
#
# The batch read fails as a whole and yq's own message goes to stderr, so
# without this the error would name all two dozen files in the set and leave
# the reader to find the broken one. Re-reading them individually costs a fork
# per file, which is affordable on a path that is already failing.
_reg_report_unparseable() {
  local file found=false
  for file in "$@"; do
    yq -N -r 'tag' "$file" >/dev/null 2>&1 && continue
    err "reg_load: could not parse $file"
    found=true
  done
  # No individual file failed, so the batch died on something else — yq's own
  # message is on stderr above, and naming the set is the most that can be said.
  $found || err "reg_load: could not read: $*"
}

# _reg_stream FILE... — the flat node stream for a set of files.
#
# A map emits its keys and a sequence its length, because `reg_keys` and
# `reg_len` answer questions no scalar record can: which fields an entry has
# (for unknown-key detection) and how many entries there are.
# The last path segment is written `#N` where the node's parent is a sequence.
# yq reports a sequence index and a numeric map key identically — both are
# `!!int` in a path — so the parent is identified by collecting every sequence
# path in the document first and testing the node's parent against that set.
# `_reg_key` builds the same form from its arguments.
_reg_stream() {
  # $seqs is every sequence's path; a segment whose parent path is in it is an
  # index and takes the `#` marker. yq has no jq-style `if/then/else`, so the
  # choice is written as a `select(...) // fallback`.
  local pathexpr="[ \$p | to_entries | .[]
        | (.key) as \$i | (.value) as \$v
        | (((\"#\" + \$v) | select([\$seqs[] | select(. == (\$p[:\$i] | join(\"$_REG_PSEP\")))] | length > 0)) // \$v)
      ] | join(\"$_REG_PSEP\")"
  # The sequence paths are filtered for non-empty: a document holding no
  # sequence at all still yields one empty-string entry, which would match the
  # empty parent path of every top-level key and mark them all as indices.
  yq -N -r -0 "
    [.. | select(tag == \"!!seq\") | [path[] | tostring] | join(\"$_REG_PSEP\") | select(. != \"\")] as \$seqs
    | (.. | select((path | length) > 0)
        | [path[] | tostring] as \$p
        | ($pathexpr) as \$key
        | (
            (select(tag != \"!!map\" and tag != \"!!seq\")
              | filename + \"$_REG_SEP\" + \$key + \"$_REG_SEP\" + tag + \"$_REG_SEP\" + tostring),
            (select(tag == \"!!map\")
              | filename + \"$_REG_SEP\" + \$key + \"$_REG_SEP!!map$_REG_SEP\" + ([keys[]|tostring]|join(\"$_REG_PSEP\"))),
            (select(tag == \"!!seq\")
              | filename + \"$_REG_SEP\" + \$key + \"$_REG_SEP!!seq$_REG_SEP\" + (length|tostring))
          ))
  " "$@"
}

# reg_invalidate FILE... — drop each FILE from the cache so the next read of it
# parses the file again.
#
# The cache is keyed by path and never expires, which is right inside one
# operation — a function handed a single file must not re-parse what its caller
# already loaded — and wrong across two. A process that outlives a registry
# being rewritten (a sync step, a test) would otherwise keep answering from the
# version it first read. The entry points that scan a directory call this, so
# each starts from what is on disk now.
reg_invalidate() {
  local file key
  for file in "$@"; do
    unset "_REG_LOADED[$file]"
  done

  # Rebuilt rather than unset key by key. `unset 'arr[$k]'` re-parses the
  # subscript, and every key here holds a \x01 the parser mangles — the entry
  # survives, and a stale node answering after an invalidate is worse than the
  # cost of the copy. Both arrays are walked from _REG_TAG's keys, which the
  # loader keeps in step with _REG_VAL's.
  local -A kept_tag=() kept_val=()
  local keep
  for key in "${!_REG_TAG[@]}"; do
    keep=true
    for file in "$@"; do
      [[ "$key" == "$file$_REG_SEP"* ]] || continue
      keep=false
      break
    done
    $keep || continue
    kept_tag[$key]="${_REG_TAG[$key]}"
    kept_val[$key]="${_REG_VAL[$key]}"
  done

  _REG_TAG=()
  _REG_VAL=()
  for key in "${!kept_tag[@]}"; do
    _REG_TAG[$key]="${kept_tag[$key]}"
    _REG_VAL[$key]="${kept_val[$key]}"
  done
}

# reg_has FILE PATH_SEGMENT... — 0 when a node exists at that path.
#
# Presence, not truthiness: an explicit `key:` with no value is present and has
# tag `!!null`. This is the `yq '... | has("x")'` it replaces, and it is the
# only way to tell absent from present-but-empty — `reg_get` returns empty for
# both, matching the `// ""` idiom every call site was written against.
reg_has() {
  local key
  key=$(_reg_key "$@")
  [[ -n "${_REG_TAG[$key]+set}" ]]
}

# reg_type FILE PATH_SEGMENT... — the YAML tag at that path, e.g. `!!str`.
#
# Empty for a path that does not exist. Replaces `yq '... | tag'`, whose answer
# for a missing node is `!!null` — a caller that needs to tell the two apart
# asks `reg_has` first, as `_check_permission_field` does.
reg_type() {
  local key
  key=$(_reg_key "$@")
  printf '%s' "${_REG_TAG[$key]:-}"
}

# reg_get FILE PATH_SEGMENT... — the scalar at that path, or empty.
#
# Empty for absent and for null alike, which is what `yq '.x // ""'` answered
# and what the `[[ -n "$x" && "$x" != "null" ]]` guards at the call sites are
# written against. Trailing newlines are stripped: a `$(yq ...)` dropped them,
# so a block scalar that kept its final newline here would come back one byte
# longer than the same read used to return.
reg_get() {
  local key
  key=$(_reg_key "$@")
  local tag="${_REG_TAG[$key]:-}"
  # A map's stored value is its key list and a sequence's is its length — both
  # are answers to `reg_keys` and `reg_len`, not scalars. Returning them here
  # would hand a caller the string "2" for a two-entry array, which reads as a
  # perfectly good value at a call site expecting one.
  [[ "$tag" == "!!null" || "$tag" == "!!map" || "$tag" == "!!seq" ]] && return 0
  local val="${_REG_VAL[$key]:-}"
  while [[ "$val" == *$'\n' ]]; do val="${val%$'\n'}"; done
  printf '%s' "$val"
}

# reg_keys FILE PATH_SEGMENT... — the child keys of a map, one per line.
#
# Document order, not sorted — `_check_unknown_fields` reports the first
# offending field, and sorting would change which one that is. Prints nothing
# for a path that is absent or is not a map.
reg_keys() {
  local key
  key=$(_reg_key "$@")
  [[ "${_REG_TAG[$key]:-}" == "!!map" ]] || return 0
  local joined="${_REG_VAL[$key]}"
  [[ -n "$joined" ]] || return 0
  printf '%s\n' "${joined//$_REG_PSEP/$'\n'}"
}

# reg_len FILE PATH_SEGMENT... — the length of a sequence, or 0 when absent.
#
# 0 for a path that does not exist, matching what `yq '.tools | length'`
# answered for a registry with no tools at all.
#
# A path that exists but is not a sequence returns 1 and prints nothing. That
# case is a `tools:` written as a mapping rather than a list, and answering 0
# for it would be the silent-skip this module exists to prevent: every entry
# loop is `for (( i=0; i<count; i++ ))`, so a count of nothing means no entry
# is checked and the file passes clean. Callers under `set -e` abort on it;
# `validate-registries` reports it as an error against the file.
reg_len() {
  local key tag
  key=$(_reg_key "$@")
  tag="${_REG_TAG[$key]:-}"
  [[ -n "$tag" ]] || { printf '0'; return 0; }
  [[ "$tag" == "!!seq" ]] || return 1
  printf '%s' "${_REG_VAL[$key]}"
}

# collect_component_registries ARRAY_REF SCAN_DIR — the component `registry.yml`
# files under a root. ARRAY_REF names the caller's array, which is replaced with
# the paths found one and two directories below SCAN_DIR, in glob order.
# SCAN_DIR is the root those globs are anchored at; a root holding none of them
# leaves the array empty rather than filling it with unexpanded patterns.
#
# Split out of `collect_registries` because `bin/local/generate-public-surface`
# needs this set on its own: it filters by the package that owns each registry,
# and must not see the `*.env.yml` and brew stack files `collect_registries` adds
# on top, since brew tools are not part of the public surface. Filtering those
# back out in the caller would only point the same coupling the other way. With
# the glob written out in both places, a change to the depth a component registry
# may live at reached the tool context and not the surface snapshot, and the
# snapshot regenerated smaller with no error from either script.
collect_component_registries() {
  local -n __components_out=$1
  local scan_dir="$2"
  local f

  __components_out=()
  for f in "$scan_dir"/*/registry.yml "$scan_dir"/*/*/registry.yml; do
    if [[ -f "$f" ]]; then
      __components_out+=("$f")
    fi
  done
}

# collect_registries ARRAY_REF SCAN_DIR [BREW_DIR]
# Populates the caller's array (via nameref) with deduplicated registry paths.
#
# SCAN_DIR: root directory to glob for */registry.yml, /*/*/registry.yml, and *.env.yml
# BREW_DIR: directory to search for *.registry.yml stacks (defaults to SCAN_DIR/brew)
collect_registries() {
  local -n _out_arr=$1
  local scan_dir="$2"
  local brew_dir="${3:-$scan_dir/brew}"

  _out_arr=()

  # Component registries (top-level + nested). First, because
  # collect_component_registries assigns to the array rather than appending.
  local -a raw=()
  collect_component_registries raw "$scan_dir"

  # Consumer-owned env files (colocated with the code that reads the vars)
  while IFS= read -r -d '' f; do
    raw+=("$f")
  done < <(find "$scan_dir" -name '*.env.yml' -not -path '*/.git/*' -print0 | sort -z)

  # Brew stack registries
  if [[ -d "$brew_dir" ]]; then
    while IFS= read -r -d '' f; do
      raw+=("$f")
    done < <(find "$brew_dir" -mindepth 2 -maxdepth 2 -name '*.registry.yml' -print0 | sort -z)
  fi

  # Deduplicate by realpath
  local -A seen=()
  local f real
  for f in "${raw[@]}"; do
    real=$(realpath "$f" 2>/dev/null || echo "$f")
    [[ -n "${seen[$real]:-}" ]] && continue
    seen[$real]=1
    _out_arr+=("$f")
  done
}

# registry_passes_install_check FILE — returns 0 if the registry should be rendered.
# Checks meta.install_check and meta.install_check_command.
registry_passes_install_check() {
  local file="$1"
  reg_load "$file" || return 1
  local install_check
  install_check=$(reg_get "$file" meta install_check)
  [[ "$install_check" == "true" ]] || return 0

  # Symlink-based check: pass if a symlink's target contains the expected string.
  # Used by registries whose relevance depends on a runtime choice (e.g. Docker runtime).
  local check_symlink check_contains
  check_symlink=$(reg_get "$file" meta install_check_symlink)
  check_contains=$(reg_get "$file" meta install_check_symlink_contains)
  if [[ -n "$check_symlink" && "$check_symlink" != "null" ]]; then
    # Expand ~ to $HOME, and the workbench roots to their resolved values.
    # Literal substitution rather than eval — the value comes from a registry
    # file, and only these three names are recognised.
    check_symlink="${check_symlink/#\~/$HOME}"
    check_symlink="${check_symlink//\$\{WORKBENCH_CONFIG_DIR\}/$WORKBENCH_CONFIG_DIR}"
    check_symlink="${check_symlink//\$\{WORKBENCH_STATE_DIR\}/$WORKBENCH_STATE_DIR}"
    check_symlink="${check_symlink//\$\{WORKBENCH_CACHE_DIR\}/$WORKBENCH_CACHE_DIR}"
    local symlink_target
    symlink_target=$(readlink "$check_symlink" 2>/dev/null || true)
    [[ "$symlink_target" == *"$check_contains"* ]] && return 0 || return 1
  fi

  # Command-based check: pass if a specific command is in PATH.
  local check_cmd
  check_cmd=$(reg_get "$file" meta install_check_command)
  if [[ -n "$check_cmd" && "$check_cmd" != "null" ]]; then
    is_installed "$check_cmd"
    return $?
  fi

  # Fallback: pass if any tool from the registry is installed
  local count i
  count=$(reg_len "$file" tools)
  for (( i=0; i<count; i++ )); do
    local name
    name=$(reg_get "$file" tools "$i" name)
    if is_installed "$name"; then
      return 0
    fi
  done
  return 1
}

# iter_registry_env FILE CALLBACK
# Calls CALLBACK var comment default_val setup_url prefix for each env[] entry.
iter_registry_env() {
  local file="$1" cb="$2"
  [[ -f "$file" ]] || return 0
  reg_load "$file" || return 1
  reg_has "$file" env || return 0

  local count i
  count=$(reg_len "$file" env)
  for (( i=0; i<count; i++ )); do
    local var comment default_val setup_url prefix
    var=$(reg_get "$file" env "$i" var)
    [[ -n "$var" && "$var" != "null" ]] || continue

    comment=$(reg_get "$file" env "$i" comment)
    default_val=$(reg_get "$file" env "$i" default)
    setup_url=$(reg_get "$file" env "$i" setup_url)
    prefix=$(reg_get "$file" env "$i" prefix)

    "$cb" "$var" "$comment" "$default_val" "$setup_url" "$prefix"
  done
}

# iter_registry_auth FILE CALLBACK
# Calls CALLBACK name env_var setup_url prefix for each tool with an auth block.
iter_registry_auth() {
  local file="$1" cb="$2"
  [[ -f "$file" ]] || return 0
  reg_load "$file" || return 1

  local count i
  count=$(reg_len "$file" tools)
  for (( i=0; i<count; i++ )); do
    local env_var
    env_var=$(reg_get "$file" tools "$i" auth env_var)
    [[ -n "$env_var" && "$env_var" != "null" ]] || continue

    local name
    name=$(reg_get "$file" tools "$i" name)

    local setup_url prefix
    setup_url=$(reg_get "$file" tools "$i" auth setup_url)
    prefix=$(reg_get "$file" tools "$i" auth prefix)

    "$cb" "$name" "$env_var" "$setup_url" "$prefix"
  done
}

# _collect_tool_permission ARRAY_REF FILE INDEX
_collect_tool_permission() {
  local -n __tool_perms=$1
  local file="$2" i="$3"
  local perm_tag perm_val name

  # An absent permission has no tag at all, where `yq '... | tag'` answered
  # !!null for it. Both mean "nothing to collect", and the empty case falls
  # through the same arm.
  perm_tag=$(reg_type "$file" tools "$i" permission)

  case "$perm_tag" in
    ''|'!!null') return 0 ;;
    '!!bool')
      perm_val=$(reg_get "$file" tools "$i" permission)
      [[ "$perm_val" == "true" ]] || return 0
      name=$(reg_get "$file" tools "$i" name)
      __tool_perms+=("Bash($name:*)")
      ;;
    '!!str')
      perm_val=$(reg_get "$file" tools "$i" permission)
      [[ -n "$perm_val" ]] || return 0
      __tool_perms+=("Bash($perm_val:*)")
      ;;
    '!!seq')
      local j arr_len entry
      arr_len=$(reg_len "$file" tools "$i" permission)
      for (( j=0; j<arr_len; j++ )); do
        entry=$(reg_get "$file" tools "$i" permission "$j")
        __tool_perms+=("$entry")
      done
      ;;
  esac
}

# collect_registry_permissions ARRAY_REF SCAN_DIR [BREW_DIR]
# Populates the caller's array (via nameref) with Claude Code Bash permission
# patterns derived from tools' permission field, one of the tool entry fields
# described in this module's header comment above.
collect_registry_permissions() {
  local _perms_var=$1
  local -n __perms_out=$1
  local scan_dir="$2"
  local brew_dir="${3:-$scan_dir/brew}"

  __perms_out=()
  local -a registries=()
  collect_registries registries "$scan_dir" "$brew_dir"
  # Invalidated first: this is a directory scan, so it answers for the tree as
  # it is now, not as some earlier call in the same process found it.
  (( ${#registries[@]} > 0 )) && { reg_invalidate "${registries[@]}"; reg_load "${registries[@]}" || return 1; }

  local file count i
  for file in "${registries[@]}"; do
    [[ -f "$file" ]] || continue
    count=$(reg_len "$file" tools)
    [[ "$count" -gt 0 ]] || continue

    for (( i=0; i<count; i++ )); do
      _collect_tool_permission "$_perms_var" "$file" "$i"
    done
  done
}

# collect_claude_env_vars SOURCES_REF TARGETS_REF SCAN_DIR [BREW_DIR]
# Populates two caller arrays (via nameref) with the env vars declared by every
# registry whose meta block sets `claude_env: true`:
#   sources — the canonical names in ~/.env.local (e.g. AI_MODEL)
#   targets — the names written into ~/.claude/settings.json (e.g. ANTHROPIC_MODEL)
# When a registry entry has no `target:` field, the target defaults to the source
# name (backward compatible with registries that predate the mapping). An entry
# carrying `claude_env: false` is skipped, so a flagged registry can hold one
# variable back without being split in two.
#
# The flag is opt-in per registry rather than a sweep of every declaration
# because the two files have different audiences: `~/.env.local` holds API keys
# and is the operator's alone, while `~/.claude/settings.json` is written 0644
# and read by every Claude Code session. Only a variable a registry has
# volunteered crosses over. Install checks are deliberately not consulted — what
# reaches the settings file is decided by what `~/.env.local` actually sets, so a
# registry gated on a tool this machine lacks contributes nothing anyway.
#
# The entry-level field runs the other way, as an opt-out, so that the audience
# question the registry-level flag forces — is every variable here safe to
# publish? — is still answered once for the whole file. An entry that says
# nothing is mirrored, so a variable is never withheld by an omission.
#
# It is read as an exact `false` rather than through yq's `// true`, which cannot
# tell a declared `false` from an absent key and would report every entry as
# opted in. Reading it this way means a value the validator would reject —
# `"flase"`, a misspelled key — includes rather than excludes, which is the
# direction that keeps a registry doing what its own flag says. What makes that
# safe is `KNOWN_ENV_FIELDS`: validate-registries rejects the unknown key and the
# non-boolean value, so neither reaches a sync.
collect_claude_env_vars() {
  local -n __sources_out=$1
  local -n __targets_out=$2
  local scan_dir="$3"
  local brew_dir="${4:-$scan_dir/brew}"

  __sources_out=()
  __targets_out=()
  local -a registries=()
  collect_registries registries "$scan_dir" "$brew_dir"
  # Invalidated first, for the reason collect_registry_permissions gives.
  (( ${#registries[@]} > 0 )) && { reg_invalidate "${registries[@]}"; reg_load "${registries[@]}" || return 1; }

  local file flagged count i var target opted_out
  for file in "${registries[@]}"; do
    [[ -f "$file" ]] || continue
    flagged=$(reg_get "$file" meta claude_env)
    [[ "$flagged" == "true" ]] || continue

    count=$(reg_len "$file" env)
    [[ "$count" -gt 0 ]] || continue

    for (( i=0; i<count; i++ )); do
      var=$(reg_get "$file" env "$i" var)
      [[ -n "$var" && "$var" != "null" ]] || continue
      opted_out=$(reg_get "$file" env "$i" claude_env)
      [[ "$opted_out" != "false" ]] || continue
      target=$(reg_get "$file" env "$i" target)
      [[ -z "$target" || "$target" == "null" ]] && target="$var"
      __sources_out+=("$var")
      __targets_out+=("$target")
    done
  done
}
