#!/usr/bin/env bats
# shellcheck shell=bats
bats_require_minimum_version 1.5.0

# Tests for the reg_* read cache in lib/registries.sh — the batched YAML reader
# that replaced a yq fork per field.
#
# The cases here are chosen for what the encoding could plausibly break rather
# than for coverage of the accessors' happy path: a scalar whose text a JSON
# round trip would rewrite, a value that is null rather than absent, a block
# scalar's trailing newline, and a file yq cannot parse. Each one is a way a
# check could stop firing while every existing test stayed green.

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/registries.sh"
}

teardown() {
  common_teardown
}

_fixture() {
  local name="$1"
  cat > "$TMPDIR/$name"
  printf '%s' "$TMPDIR/$name"
}

@test "reg_get returns scalars and reg_len counts a sequence" {
  local f
  f=$(_fixture reg.yml <<'EOF'
meta:
  section: Tools
tools:
  - name: alpha
    description: first
  - name: beta
    description: second
EOF
)
  reg_load "$f"
  [ "$(reg_get "$f" meta section)" = "Tools" ]
  [ "$(reg_len "$f" tools)" = "2" ]
  [ "$(reg_get "$f" tools 0 name)" = "alpha" ]
  [ "$(reg_get "$f" tools 1 description)" = "second" ]
}

@test "reg_len fails on a collection that is not a list" {
  # The silent-skip this module exists to prevent. Every entry loop is
  # `for (( i=0; i<count; i++ ))`, so answering 0 for a `tools:` written as a
  # mapping means no entry is examined and the file validates clean.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  alpha:
    permission: false
  beta:
    permission: false
EOF
)
  reg_load "$f"
  run reg_len "$f" tools
  [ "$status" -ne 0 ]
  [ -z "$output" ]
  [ "$(reg_type "$f" tools)" = "!!map" ]
}

@test "a map keyed by digits cannot impersonate a list entry" {
  # yq renders a sequence index and a literal map key of the same digits
  # identically in a path, so the index carries a marker. Without it a
  # `tools: {0: {...}}` would answer a read written for a list, and the entry
  # would be validated as though it were one.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  0:
    name: not-a-list-entry
EOF
)
  reg_load "$f"
  [ -z "$(reg_get "$f" tools 0 name)" ]
  [ "$(reg_type "$f" tools)" = "!!map" ]
}

@test "a numeric index reads the list entry, not a same-named key" {
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: from-the-list
EOF
)
  reg_load "$f"
  [ "$(reg_get "$f" tools 0 name)" = "from-the-list" ]
  [ "$(reg_len "$f" tools)" = "1" ]
}

@test "a key holding a delimiter byte is rejected, not mis-parsed" {
  # The record splits on \x01, so one inside a key lands the split mid-key and
  # every field after it is garbage — the tag slot ends up holding part of the
  # key, and reg_type answers something that is not a YAML tag at all.
  local f="$TMPDIR/ctrl.yml"
  printf 'meta:\n  "we\\x01ird": v\n' > "$f"
  run reg_load "$f"
  [ "$status" -ne 0 ]
  [[ "$output" == *"delimiter byte"* ]]
}

@test "a file whose load failed is not marked loaded" {
  # Marking on the way in would leave a failed file marked, and every later
  # read of it would answer empty rather than re-reading or failing again.
  local f="$TMPDIR/ctrl.yml"
  printf 'meta:\n  "we\\x01ird": v\n' > "$f"
  run reg_load "$f"
  [ "$status" -ne 0 ]
  # Repair the file; a second load must actually read it.
  printf 'meta:\n  section: Fixed\n' > "$f"
  reg_load "$f"
  [ "$(reg_get "$f" meta section)" = "Fixed" ]
}

@test "reg_len is 0 for a sequence that is absent, not an error" {
  # `yq '.tools | length'` answered 0 for a registry with no tools, and the
  # entry loops are written as `for (( i=0; i<count; i++ ))` against it.
  local f
  f=$(_fixture reg.yml <<'EOF'
meta:
  section: Tools
EOF
)
  reg_load "$f"
  [ "$(reg_len "$f" tools)" = "0" ]
}

@test "a scalar's text survives exactly as the file spells it" {
  # The reason the stream never passes through jq. JSON normalises numbers:
  # 1e3 comes back 1E+3, 0x1F comes back 31, 00123 comes back 123. Each of
  # those is a different string at a call site that compares or prints it.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
    float_text: 1.10
    exponent: 1e3
    hex: 0x1F
    padded: 00123
EOF
)
  reg_load "$f"
  [ "$(reg_get "$f" tools 0 float_text)" = "1.10" ]
  [ "$(reg_get "$f" tools 0 exponent)" = "1e3" ]
  [ "$(reg_get "$f" tools 0 hex)" = "0x1F" ]
  [ "$(reg_get "$f" tools 0 padded)" = "00123" ]
}

@test "reg_type reports the YAML tag the validator branches on" {
  # _check_permission_field dispatches on this and prints it in its error, so
  # !!int and !!float are not interchangeable and `yes` must stay a string —
  # YAML 1.1 readers call it a bool, YAML 1.2 (which yq is) does not.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
    b: true
    s: hello
    i: 42
    fl: 1.5
    yes_str: yes
    arr: ["one", "two"]
EOF
)
  reg_load "$f"
  [ "$(reg_type "$f" tools 0 b)" = "!!bool" ]
  [ "$(reg_type "$f" tools 0 s)" = "!!str" ]
  [ "$(reg_type "$f" tools 0 i)" = "!!int" ]
  [ "$(reg_type "$f" tools 0 fl)" = "!!float" ]
  [ "$(reg_type "$f" tools 0 yes_str)" = "!!str" ]
  [ "$(reg_type "$f" tools 0 arr)" = "!!seq" ]
}

@test "an explicit null is present but reads empty" {
  # The distinction every required-field check rests on. `description:` with
  # no value must still fail the check, so reg_get answers empty for it — but
  # reg_has must say it is there, which is what `has("x")` answered.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
    description:
EOF
)
  reg_load "$f"
  reg_has "$f" tools 0 description
  [ "$(reg_type "$f" tools 0 description)" = "!!null" ]
  [ -z "$(reg_get "$f" tools 0 description)" ]
}

@test "every spelling of null reads empty, not as its own text" {
  # This is what the !!null arm in reg_get is for, and it is not redundant:
  # `~` stringifies to "~" and `null` to "null". Either would satisfy an
  # `[[ -n "$x" ]]` required-field guard — the tilde would satisfy the
  # `!= "null"` half too — so a missing field would read as a present one.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
    bare:
    tilde: ~
    word: null
    quoted: "null"
EOF
)
  reg_load "$f"
  local field
  for field in bare tilde word; do
    [ "$(reg_type "$f" tools 0 "$field")" = "!!null" ]
    [ -z "$(reg_get "$f" tools 0 "$field")" ]
  done
  # A quoted "null" is a string a registry meant to write, and survives.
  [ "$(reg_get "$f" tools 0 quoted)" = "null" ]
}

@test "an absent field is absent, and reads empty too" {
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
EOF
)
  reg_load "$f"
  run ! reg_has "$f" tools 0 description
  [ -z "$(reg_get "$f" tools 0 description)" ]
  [ -z "$(reg_type "$f" tools 0 description)" ]
}

@test "reg_get matches what a per-field yq read returned, byte for byte" {
  # The equivalence the whole change rests on, asserted against yq itself
  # rather than against a literal — including a block scalar, whose trailing
  # newline `$(yq ...)` stripped and the raw stream does not.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
    block: |
      line one
      line two
    trailing: "ends with spaces   "
    dashes: "-- not a flag"
EOF
)
  reg_load "$f"
  local field
  for field in name block trailing dashes; do
    [ "$(reg_get "$f" tools 0 "$field")" = "$(yq ".tools[0].$field" "$f")" ]
  done
}

@test "a value holding a tab survives the field delimiter" {
  # Written with printf rather than through _fixture: the fixture bodies are
  # heredocs, and a literal tab in one is at the mercy of whatever strips
  # whitespace between here and the file. This test is about that exact byte.
  local f
  f=$(printf 'tools:\n  - name: "has\ttab"\n' > "$TMPDIR/reg.yml"; printf '%s' "$TMPDIR/reg.yml")
  reg_load "$f"
  [ "$(reg_get "$f" tools 0 name)" = "$(yq '.tools[0].name' "$f")" ]
  [[ "$(reg_get "$f" tools 0 name)" == *$'\t'* ]]
}

@test "reg_keys lists a map's fields in document order" {
  # _check_unknown_fields reports the first offending field, so sorting these
  # would change which field a failing registry is blamed for.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
    zebra: z
    apple: b
EOF
)
  reg_load "$f"
  [ "$(reg_keys "$f" tools 0 | tr '\n' ' ')" = "name zebra apple " ]
}

@test "reg_keys prints nothing for a scalar or a missing path" {
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
EOF
)
  reg_load "$f"
  [ -z "$(reg_keys "$f" tools 0 name)" ]
  [ -z "$(reg_keys "$f" nothing)" ]
}

@test "reg_get declines to stringify a map or a sequence" {
  # Their stored value is a key list and a length — reg_keys and reg_len own
  # those. Handing "2" back for a two-entry array would read as a valid scalar
  # at a call site that asked for one.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
    arr: ["one", "two"]
    obj:
      k: v
EOF
)
  reg_load "$f"
  [ -z "$(reg_get "$f" tools 0 arr)" ]
  [ -z "$(reg_get "$f" tools 0 obj)" ]
  [ "$(reg_len "$f" tools 0 arr)" = "2" ]
  [ "$(reg_get "$f" tools 0 arr 1)" = "two" ]
}

# ── the fork-free accessors ──────────────────────────────────────────────────
# reg_get_into, reg_type_into and reg_len_into exist so a hot loop stops paying
# a command substitution per read. They are only worth having if they answer
# exactly what their forking siblings answer, so that is what is asserted —
# against the same node, rather than against a value spelled twice.

@test "the _into accessors answer what their forking siblings answer" {
  local f
  f=$(_fixture reg.yml <<'EOF'
meta:
  section: Tools
  empty:
tools:
  - name: alpha
    arr: ["one", "two"]
    obj:
      k: v
EOF
)
  reg_load "$f"

  # Every node shape the encoding distinguishes: a scalar, an explicit null, a
  # map, a sequence, a sequence entry, and a path that is not there at all.
  local -a paths=(
    "meta section" "meta empty" "meta" "tools" "tools 0" "tools 0 name"
    "tools 0 arr" "tools 0 arr 1" "tools 0 obj" "nope" "tools 9 name"
  )
  local p fork into
  local -a segs
  for p in "${paths[@]}"; do
    read -ra segs <<< "$p"
    fork=$(reg_get "$f" "${segs[@]}")
    reg_get_into into "$f" "${segs[@]}"
    [ "$fork" = "$into" ] || { echo "reg_get_into [$p]: fork=[$fork] into=[$into]"; return 1; }

    fork=$(reg_type "$f" "${segs[@]}")
    reg_type_into into "$f" "${segs[@]}"
    [ "$fork" = "$into" ] || { echo "reg_type_into [$p]: fork=[$fork] into=[$into]"; return 1; }
  done
}

@test "reg_len_into reports a non-sequence the way reg_len does" {
  # reg_len returns 1 and prints nothing for a path holding something that is
  # not a list — the case a caller under `set -e` is meant to abort on. An
  # _into variant that answered 0 there would turn that abort into a silent
  # zero-entry loop, which is the failure the whole module exists to prevent.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  first:
    name: a
list:
  - one
  - two
EOF
)
  reg_load "$f"

  local n status
  reg_len_into n "$f" list
  [ "$n" = "2" ]

  # Absent and explicit-null alike are 0 entries, not an error.
  reg_len_into n "$f" nothing
  [ "$n" = "0" ]

  status=0
  reg_len_into n "$f" tools || status=$?
  [ "$status" -eq 1 ]
}

@test "an _into accessor writes the caller's variable, not its own local" {
  # $1 is a nameref to a caller variable, so a local sharing that name is the
  # one the nameref resolves to — and the caller is handed back an empty string
  # with no error. `key`, `tag` and `val` were the accessors' own locals and all
  # three failed this way before they were prefixed.
  local f
  f=$(_fixture reg.yml <<'EOF'
meta:
  section: Tools
tools:
  - name: alpha
EOF
)
  reg_load "$f"

  local name
  for name in key tag val file path seg count; do
    unset "$name"
    reg_get_into "$name" "$f" meta section
    [ "${!name}" = "Tools" ] || { echo "reg_get_into into '\$$name' gave [${!name}]"; return 1; }

    unset "$name"
    reg_type_into "$name" "$f" meta section
    [ "${!name}" = "!!str" ] || { echo "reg_type_into into '\$$name' gave [${!name}]"; return 1; }

    unset "$name"
    reg_len_into "$name" "$f" tools
    [ "${!name}" = "1" ] || { echo "reg_len_into into '\$$name' gave [${!name}]"; return 1; }
  done
}

@test "one load serves several files without their paths colliding" {
  local a b
  a=$(_fixture a.yml <<'EOF'
tools:
  - name: from-a
EOF
)
  b=$(_fixture b.yml <<'EOF'
tools:
  - name: from-b
EOF
)
  reg_load "$a" "$b"
  [ "$(reg_get "$a" tools 0 name)" = "from-a" ]
  [ "$(reg_get "$b" tools 0 name)" = "from-b" ]
}

@test "reg_load is idempotent and skips a file already cached" {
  # Functions that take a single file call reg_load on it, so a caller that
  # already loaded the batch must not pay for a second parse.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
EOF
)
  reg_load "$f"
  # Break the file on disk: a second load that re-read it would fail, and a
  # second load that re-parsed it would replace the cached value.
  printf 'tools: [\n' > "$f"
  reg_load "$f"
  [ "$(reg_get "$f" tools 0 name)" = "a" ]
}

@test "reg_invalidate makes the next read parse the file again" {
  # The cache never expires, which is right inside one operation and wrong
  # across two: a process that outlives a registry being rewritten — a sync
  # step, or a test that writes a fixture twice — would keep answering from the
  # version it first read.
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: first
EOF
)
  reg_load "$f"
  [ "$(reg_get "$f" tools 0 name)" = "first" ]

  printf 'tools:\n  - name: second\n' > "$f"
  reg_load "$f"
  [ "$(reg_get "$f" tools 0 name)" = "first" ]

  reg_invalidate "$f"
  reg_load "$f"
  [ "$(reg_get "$f" tools 0 name)" = "second" ]
}

@test "reg_invalidate drops a file's nodes, not another file's" {
  local a b
  a=$(_fixture a.yml <<'EOF'
tools:
  - name: from-a
EOF
)
  b=$(_fixture b.yml <<'EOF'
tools:
  - name: from-b
EOF
)
  reg_load "$a" "$b"
  reg_invalidate "$a"
  [ -z "$(reg_get "$a" tools 0 name)" ]
  [ "$(reg_get "$b" tools 0 name)" = "from-b" ]
}

# ── the shared scan ───────────────────────────────────────────────────────────
# A collector re-scans the tree on every call. `reg_scan_hold` lets one logical
# operation share a single scan across the collectors it runs — the cost being
# that a rewrite inside the block is not seen, which is why the block is bounded
# and why the boundary is what these cases pin.

# _scan_dir_with CONTENT — a scan directory holding one component registry.
#
# The registry goes a level down: collect_component_registries globs
# `$scan_dir/*/registry.yml`, so a file at the root of the scan directory is
# not found at all and every collector would answer empty.
_scan_dir_with() {
  local dir="$TMPDIR/scan"
  mkdir -p "$dir/component"
  cat > "$dir/component/registry.yml"
  printf '%s' "$dir"
}

# passes-at-base: pins the invalidate-every-call behaviour the hold left intact
@test "a collector outside a hold sees a registry rewritten since the last call" {
  # The unheld path, and the reason a collector invalidates at all: a sync step
  # that outlives an edit must answer for the tree as it is now.
  local dir
  dir=$(_scan_dir_with <<'EOF'
meta:
  claude_env: true
env:
  - var: FIRST_VAR
EOF
)
  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$dir" "$dir/brew"
  [ "${sources[0]}" = "FIRST_VAR" ]

  cat > "$dir/component/registry.yml" <<'EOF'
meta:
  claude_env: true
env:
  - var: SECOND_VAR
EOF
  collect_claude_env_vars sources targets "$dir" "$dir/brew"
  [ "${sources[0]}" = "SECOND_VAR" ]
}

@test "two collectors in one hold read the same scan" {
  # Asserted through the hold's own cost rather than its benefit. A second
  # collector that reused the first's load cannot see a rewrite made between
  # them, and a second collector that re-scanned can — so the rewrite is the
  # one observable difference between sharing and not sharing.
  #
  # Checking only that both collectors return the right values would pass
  # either way, which makes it a test of the collectors and not of the hold:
  # a hold that silently stopped sharing would still be green, and the
  # optimisation would be gone with nothing to say so.
  local dir
  dir=$(_scan_dir_with <<'EOF'
meta:
  claude_env: true
tools:
  - name: alpha
    permission: true
env:
  - var: HELD_VAR
EOF
)
  local -a perms=() sources=() targets=()
  _both() {
    collect_registry_permissions perms "$dir" "$dir/brew"
    cat > "$dir/component/registry.yml" <<'EOF'
meta:
  claude_env: true
env:
  - var: REWRITTEN_MID_HOLD
EOF
    collect_claude_env_vars sources targets "$dir" "$dir/brew"
  }
  reg_scan_hold _both

  [ "${perms[0]}" = "Bash(alpha:*)" ]
  # The scan the hold is holding, not the file as it now stands.
  [ "${sources[0]}" = "HELD_VAR" ]
}

@test "a hold does not outlive the block it wrapped" {
  # The bound that keeps the ceiling honest. A hold that leaked would turn
  # every later collector in the process into a stale read — the exact failure
  # reg_invalidate exists to prevent, reintroduced by the optimisation.
  local dir
  dir=$(_scan_dir_with <<'EOF'
meta:
  claude_env: true
env:
  - var: BEFORE_VAR
EOF
)
  local -a sources=() targets=()
  _once() { collect_claude_env_vars sources targets "$dir" "$dir/brew"; }
  reg_scan_hold _once
  [ "${sources[0]}" = "BEFORE_VAR" ]

  cat > "$dir/component/registry.yml" <<'EOF'
meta:
  claude_env: true
env:
  - var: AFTER_VAR
EOF
  # Outside the hold now, so this must re-scan.
  collect_claude_env_vars sources targets "$dir" "$dir/brew"
  [ "${sources[0]}" = "AFTER_VAR" ]
}

@test "a hold is released even when the block fails" {
  # `reg_scan_hold cmd` propagates cmd's status, and a non-zero one must not
  # leave the hold open behind it.
  local dir
  dir=$(_scan_dir_with <<'EOF'
meta:
  claude_env: true
env:
  - var: ONLY_VAR
EOF
)
  _fails() { return 3; }
  local status=0
  reg_scan_hold _fails || status=$?
  [ "$status" -eq 3 ]

  cat > "$dir/component/registry.yml" <<'EOF'
meta:
  claude_env: true
env:
  - var: REWRITTEN_VAR
EOF
  # shellcheck disable=SC2034  # filled by nameref in collect_claude_env_vars
  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$dir" "$dir/brew"
  [ "${sources[0]}" = "REWRITTEN_VAR" ]

  # A second read, because one does not distinguish a released hold from a
  # leaked one. A leaked flag arrives with an empty key map, so the first
  # collector after it finds its key unheld, re-scans, and answers freshly —
  # while recording the key. Only the read after that one sees the stale
  # answer, which is the failure this case is named for.
  cat > "$dir/component/registry.yml" <<'EOF'
meta:
  claude_env: true
env:
  - var: REWRITTEN_TWICE
EOF
  collect_claude_env_vars sources targets "$dir" "$dir/brew"
  [ "${sources[0]}" = "REWRITTEN_TWICE" ]
}

@test "reg_load fails loudly on a file yq cannot parse" {
  # Stricter than the per-field reads it replaces, deliberately: those returned
  # an empty count for a malformed file, the entry loop ran zero times, and
  # every check on that file passed having read nothing.
  local f
  f=$(_fixture broken.yml <<'EOF'
tools: [
EOF
)
  run reg_load "$f"
  [ "$status" -ne 0 ]
  [[ "$output" == *"broken.yml"* ]]
}

@test "a failed batch names the file that could not be parsed" {
  # The read is one yq over the whole set, so the failure is the batch's. The
  # error has to name the broken file and not the two dozen that were merely
  # along for the ride — the per-file reads this replaced always named it.
  local good bad
  good=$(_fixture good.yml <<'EOF'
meta:
  section: fine
EOF
)
  bad=$(_fixture broken.yml <<'EOF'
tools: [
EOF
)
  run reg_load "$good" "$bad"
  [ "$status" -ne 0 ]
  [[ "$output" == *"broken.yml"* ]]
  [[ "$output" != *"good.yml"* ]]
}

@test "a zero-byte registry loads and reads as empty" {
  local f="$TMPDIR/empty.yml"
  : > "$f"
  run reg_load "$f"
  [ "$status" -eq 0 ]
  reg_load "$f"
  [ "$(reg_len "$f" tools)" = "0" ]
  run ! reg_has "$f" meta
}

@test "a comment-only registry loads and reads as empty" {
  local f
  f=$(_fixture comment.yml <<'EOF'
# nothing but a comment
EOF
)
  reg_load "$f"
  [ "$(reg_len "$f" tools)" = "0" ]
}

@test "reg_load leaves no temp file behind" {
  local f
  f=$(_fixture reg.yml <<'EOF'
tools:
  - name: a
EOF
)
  local scratch="$TMPDIR/scratch"
  mkdir -p "$scratch"
  TMPDIR="$scratch" reg_load "$f"
  [ -z "$(ls -A "$scratch")" ]
}
