#!/usr/bin/env bats
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
  TMPDIR="$(mktemp -d)"
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/registries.sh"
}

teardown() {
  rm -rf "$TMPDIR"
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
  ! reg_has "$f" tools 0 description
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
  ! reg_has "$f" meta
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
