#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  GENERATOR="$REPO_ROOT/bin/local/generate-public-surface"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _generator_under_test — prints the generator the fault-injection tests run.
# GENERATOR_UNDER_TEST points the same test at a copy of the generator with the
# guard under test removed: if the test still passes against that copy it is
# not discriminating the bug it claims to, only asserting something true of any
# implementation. Each fault-injection test below was run both ways once.
_generator_under_test() {
  echo "${GENERATOR_UNDER_TEST:-$GENERATOR}"
}

# _shim_failure BINARY MATCH MESSAGE — installs a fake BINARY that fails with
# MESSAGE when any argument contains MATCH and execs the real one otherwise,
# then prints the directory to put in front of PATH.
#
# Failing one call site and not the whole binary is the point of the MATCH
# argument. A total tool outage is already caught by _collect's empty-category
# check, so a shim that failed unconditionally would abort against any
# implementation and discriminate nothing; each caller passes a fragment unique
# to the single call it is targeting.
_shim_failure() {
  local binary="$1" match="$2" message="$3"
  local fakebin="$TMPDIR/fakebin" real
  mkdir -p "$fakebin"
  real="$(command -v "$binary")"
  cat > "$fakebin/$binary" <<EOF
#!/usr/bin/env bash
for a in "\$@"; do
  if [[ "\$a" == *"$match"* ]]; then
    echo "$message" >&2
    exit 1
  fi
done
exec "$real" "\$@"
EOF
  chmod +x "$fakebin/$binary"
  echo "$fakebin"
}

@test "generator writes both package snapshots" {
  run "$GENERATOR" --out-dir "$TMPDIR" --quiet
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/public-surface.json" ]
  [ -f "$TMPDIR/ai/claude/public-surface.json" ]
}

@test "root snapshot names the otto-workbench package" {
  "$GENERATOR" --out-dir "$TMPDIR" --quiet
  run jq -r '.package' "$TMPDIR/public-surface.json"
  [ "$output" = "otto-workbench" ]
}

@test "ai snapshot names the otto-ai-tools package" {
  "$GENERATOR" --out-dir "$TMPDIR" --quiet
  run jq -r '.package' "$TMPDIR/ai/claude/public-surface.json"
  [ "$output" = "otto-ai-tools" ]
}

@test "root snapshot carries commands, config keys, and components" {
  "$GENERATOR" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries[]' "$TMPDIR/public-surface.json"
  [[ "$output" == *"command:get-secret"* ]]
  [[ "$output" == *"config:reuse.level"* ]]
  [[ "$output" == *"config:reuse.level=ultra"* ]]
  [[ "$output" == *"component:brew"* ]]
}

@test "workbench-scoped tools are not public" {
  "$GENERATOR" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries[]' "$TMPDIR/public-surface.json"
  [[ "$output" != *"command:validate-all"* ]]
}

@test "ai/claude tools land in the ai snapshot, not the root one" {
  "$GENERATOR" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries[]' "$TMPDIR/ai/claude/public-surface.json"
  [[ "$output" == *"command:claude-review"* ]]
  [[ "$output" == *"agent:debugger"* ]]
  [[ "$output" == *"skill:pr-comments"* ]]
  [[ "$output" == *"setting:hooks"* ]]
  run jq -r '.entries[]' "$TMPDIR/public-surface.json"
  [[ "$output" != *"command:claude-review"* ]]
}

@test "entries are sorted and unique" {
  "$GENERATOR" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries == (.entries | sort | unique)' "$TMPDIR/public-surface.json"
  [ "$output" = "true" ]
}

@test "--check passes against the committed snapshots" {
  run "$GENERATOR" --check --quiet
  [ "$status" -eq 0 ]
}

@test "--check fails on a stale snapshot and never writes to it" {
  mkdir -p "$TMPDIR/ai/claude"
  cp "$REPO_ROOT/public-surface.json" "$TMPDIR/public-surface.json"
  cp "$REPO_ROOT/ai/claude/public-surface.json" "$TMPDIR/ai/claude/public-surface.json"
  jq '.entries += ["command:not-a-real-tool"]' "$TMPDIR/public-surface.json" > "$TMPDIR/mutated.json"
  mv "$TMPDIR/mutated.json" "$TMPDIR/public-surface.json"
  cp "$TMPDIR/public-surface.json" "$TMPDIR/before.json"

  run "$GENERATOR" --check --quiet --out-dir "$TMPDIR"
  [ "$status" -eq 1 ]

  run cmp "$TMPDIR/public-surface.json" "$TMPDIR/before.json"
  [ "$status" -eq 0 ]
}

@test "generation is deterministic regardless of the caller's locale" {
  local dir_c="$TMPDIR/c" dir_en="$TMPDIR/en"
  env LC_ALL=C "$GENERATOR" --out-dir "$dir_c" --quiet
  env LC_ALL=en_US.UTF-8 "$GENERATOR" --out-dir "$dir_en" --quiet

  run cmp "$dir_c/public-surface.json" "$dir_en/public-surface.json"
  [ "$status" -eq 0 ]
  run cmp "$dir_c/ai/claude/public-surface.json" "$dir_en/ai/claude/public-surface.json"
  [ "$status" -eq 0 ]
}

@test "ai/serena tools land in the root snapshot, not ai/claude" {
  "$GENERATOR" --out-dir "$TMPDIR" --quiet
  run jq -e '.entries | index("command:serena-mcp")' "$TMPDIR/public-surface.json"
  [ "$status" -eq 0 ]
  run jq -e '.entries | index("command:serena-mcp")' "$TMPDIR/ai/claude/public-surface.json"
  [ "$status" -eq 1 ]
}

@test "every ai/claude registry tool has a matching command entry" {
  "$GENERATOR" --out-dir "$TMPDIR" --quiet
  while IFS= read -r name; do
    run jq -e --arg e "command:$name" '.entries | index($e)' "$TMPDIR/ai/claude/public-surface.json"
    [ "$status" -eq 0 ]
  done < <(yq -r '.tools[].name' "$REPO_ROOT/ai/claude/registry.yml")
}

@test "every ai/claude agent has a matching agent entry" {
  "$GENERATOR" --out-dir "$TMPDIR" --quiet
  for f in "$REPO_ROOT"/ai/claude/agents/*.md; do
    name="$(basename "$f" .md)"
    run jq -e --arg e "agent:$name" '.entries | index($e)' "$TMPDIR/ai/claude/public-surface.json"
    [ "$status" -eq 0 ]
  done
}

@test "a broken jq call aborts instead of writing a truncated snapshot" {
  local gen fakebin
  gen="$(_generator_under_test)"
  # "def props(" is the _config_entries jq program and nothing else, so this
  # breaks one pipeline stage among several rather than the whole tool.
  fakebin="$(_shim_failure jq 'def props(' 'jq: 1 compile error')"

  run env PATH="$fakebin:$PATH" "$gen" --out-dir "$TMPDIR/out" --quiet
  [ "$status" -ne 0 ]
  [ ! -e "$TMPDIR/out" ]
  # Exit 1 alone is not enough: --check uses 1 for "the snapshot is stale",
  # which sends the contributor to this same script to fix a broken jq by
  # re-running it. The abort has to name the category that failed.
  [[ "$output" == *"config entries failed"* ]]
}

@test "a broken yq call inside _registries_for aborts instead of truncating" {
  local gen fakebin
  gen="$(_generator_under_test)"
  # _registries_for's own yq calls are reached through a process substitution
  # (`done < <(_registries_for ...)` in _commands), which has no exit-status
  # channel back to the reader at all — pipefail and inherit_errexit both
  # only reach pipes and command substitutions, neither of which this is.
  # One registry and not every one: a total outage would make _registries_for
  # fail on its first iteration, which _collect's empty-category check already
  # catches, so it would not discriminate this bug.
  fakebin="$(_shim_failure yq 'git/bin/registry.yml' 'yq: fake failure on git/bin/registry.yml')"

  run env PATH="$fakebin:$PATH" "$gen" --out-dir "$TMPDIR/out" --quiet
  [ "$status" -ne 0 ]
  [ ! -e "$TMPDIR/out" ]
  # See the note on the jq test above — a silent exit 1 reads as a stale
  # snapshot, and re-running the generator "to fix it" exits 1 again.
  [[ "$output" == *"_registries_for failed for otto-workbench"* ]]
}

@test "a broken jq call inside _write_snapshot's render pipeline aborts instead of writing an empty snapshot" {
  local gen fakebin
  gen="$(_generator_under_test)"
  # _write_snapshot's own render pipeline (printf | sort | jq -R | jq -s) is
  # only reachable if _write_snapshot itself runs under errexit, which
  # requires it not be called on the left of || at its call sites. Failing
  # only the `select(length > 0)` stage (unique to _write_snapshot, not used
  # by any category function) isolates this specific pipeline.
  fakebin="$(_shim_failure jq 'select(length > 0)' 'jq: fake failure on select(length > 0)')"

  run env PATH="$fakebin:$PATH" "$gen" --out-dir "$TMPDIR/out" --quiet
  [ "$status" -ne 0 ]
  [ ! -e "$TMPDIR/out/public-surface.json" ]
  [ ! -e "$TMPDIR/out/ai/claude/public-surface.json" ]
}

# A git hook exports GIT_DIR, and with one set git skips discovery: the
# `git -C "$(dirname "$_SELF")" rev-parse --show-toplevel` these scripts used to
# resolve their own root answered bin/local, so sourcing lib/ui.sh failed and
# pre-push died before it could run the gate at all. Nothing resolves the root
# through git any more, so pointing GIT_DIR at a path that is not a git
# directory is the strongest form of the check — any surviving git-based
# resolution fails outright against it.
@test "the generator resolves its own root with GIT_DIR exported" {
  run env GIT_DIR="$TMPDIR/nowhere" "$GENERATOR" --out-dir "$TMPDIR/out" --quiet
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/out/public-surface.json" ]
  [ -f "$TMPDIR/out/ai/claude/public-surface.json" ]
}

@test "the validator resolves its own root with GIT_DIR exported" {
  run env GIT_DIR="$TMPDIR/nowhere" "$REPO_ROOT/bin/local/validate-public-surface" --quiet
  [ "$status" -eq 0 ]
}

@test "the generator resolves its own root from an unrelated directory" {
  cd /
  run "$GENERATOR" --out-dir "$TMPDIR/out" --quiet
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/out/public-surface.json" ]
}

@test "config keys and enums nested under allOf reach the snapshot" {
  local gen
  gen="$(_generator_under_test)"
  local fakebin="$TMPDIR/fakebin" real_jq
  mkdir -p "$fakebin"
  real_jq="$(command -v jq)"
  # config.schema.json uses no allOf today, so the walk can only be exercised
  # against a fixture. This shim swaps the schema path for exactly the two
  # _config_entries jq calls (identified by a fragment of their programs) and
  # passes every other invocation through untouched — rather than an override
  # flag on the generator that nothing in production would ever set.
  #
  # Hand-rolled rather than built on _shim_failure: that helper makes the
  # matched call fail, and this one has to succeed against different input.
  # Rewriting an argument and refusing to run are different shims that happen
  # to share their scaffolding.
  cat > "$TMPDIR/schema.json" <<'JSON'
{
  "properties": {"plain": {"type": "string"}},
  "allOf": [
    {"properties": {"composed": {"type": "string", "enum": ["one"]}}}
  ]
}
JSON
  cat > "$fakebin/jq" <<EOF
#!/usr/bin/env bash
saw_program=false
args=()
for a in "\$@"; do
  if [[ "\$a" == *"def props("* || "\$a" == *"def enums("* ]]; then
    saw_program=true
  elif [[ "\$saw_program" == true && "\$a" == *"/config.schema.json" ]]; then
    a="$TMPDIR/schema.json"
  fi
  args+=("\$a")
done
exec "$real_jq" "\${args[@]}"
EOF
  chmod +x "$fakebin/jq"

  run env PATH="$fakebin:$PATH" "$gen" --out-dir "$TMPDIR/out" --quiet
  [ "$status" -eq 0 ]
  run jq -e '.entries | index("config:plain")' "$TMPDIR/out/public-surface.json"
  [ "$status" -eq 0 ]
  # The two the pre-fix walk dropped: a key reachable only through allOf, and
  # its enum values.
  run jq -e '.entries | index("config:composed")' "$TMPDIR/out/public-surface.json"
  [ "$status" -eq 0 ]
  run jq -e '.entries | index("config:composed=one")' "$TMPDIR/out/public-surface.json"
  [ "$status" -eq 0 ]
}

# Exit 2 for a usage error, not 1: --check reserves 1 for "the snapshot is
# stale", so a mistyped flag exiting 1 sends the contributor off to regenerate
# a snapshot that was never out of date.
@test "--out-dir with no directory is a usage error, not a stale snapshot" {
  run "$GENERATOR" --out-dir
  [ "$status" -eq 2 ]
  [[ "$output" == *"--out-dir requires a directory"* ]]
}

@test "an unknown argument is a usage error, not a stale snapshot" {
  run "$GENERATOR" --nope
  [ "$status" -eq 2 ]
  [[ "$output" == *"Unknown argument"* ]]
}

@test "--out-dir followed by a flag is a usage error, not a directory name" {
  run "$GENERATOR" --out-dir --quiet
  [ "$status" -eq 2 ]
  [[ "$output" == *"--out-dir requires a directory"* ]]
}

@test "validator's own unknown argument is a usage error, not a stale snapshot" {
  run "$REPO_ROOT/bin/local/validate-public-surface" --nope
  [ "$status" -eq 2 ]
  [[ "$output" == *"Unknown argument"* ]]
}
