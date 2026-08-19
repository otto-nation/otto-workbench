#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

@test "generator writes both package snapshots" {
  run "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/public-surface.json" ]
  [ -f "$TMPDIR/ai/claude/public-surface.json" ]
}

@test "root snapshot names the otto-workbench package" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.package' "$TMPDIR/public-surface.json"
  [ "$output" = "otto-workbench" ]
}

@test "ai snapshot names the otto-ai-tools package" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.package' "$TMPDIR/ai/claude/public-surface.json"
  [ "$output" = "otto-ai-tools" ]
}

@test "root snapshot carries commands, config keys, and components" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries[]' "$TMPDIR/public-surface.json"
  [[ "$output" == *"command:get-secret"* ]]
  [[ "$output" == *"config:reuse.level"* ]]
  [[ "$output" == *"config:reuse.level=ultra"* ]]
  [[ "$output" == *"component:brew"* ]]
}

@test "workbench-scoped tools are not public" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries[]' "$TMPDIR/public-surface.json"
  [[ "$output" != *"command:validate-all"* ]]
}

@test "ai/claude tools land in the ai snapshot, not the root one" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries[]' "$TMPDIR/ai/claude/public-surface.json"
  [[ "$output" == *"command:claude-review"* ]]
  [[ "$output" == *"agent:debugger"* ]]
  [[ "$output" == *"skill:pr-comments"* ]]
  [[ "$output" == *"setting:hooks"* ]]
  run jq -r '.entries[]' "$TMPDIR/public-surface.json"
  [[ "$output" != *"command:claude-review"* ]]
}

@test "entries are sorted and unique" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -r '.entries == (.entries | sort | unique)' "$TMPDIR/public-surface.json"
  [ "$output" = "true" ]
}

@test "--check passes against the committed snapshots" {
  run "$REPO_ROOT/bin/local/generate-public-surface" --check --quiet
  [ "$status" -eq 0 ]
}

@test "--check fails on a stale snapshot and never writes to it" {
  mkdir -p "$TMPDIR/ai/claude"
  cp "$REPO_ROOT/public-surface.json" "$TMPDIR/public-surface.json"
  cp "$REPO_ROOT/ai/claude/public-surface.json" "$TMPDIR/ai/claude/public-surface.json"
  jq '.entries += ["command:not-a-real-tool"]' "$TMPDIR/public-surface.json" > "$TMPDIR/mutated.json"
  mv "$TMPDIR/mutated.json" "$TMPDIR/public-surface.json"
  cp "$TMPDIR/public-surface.json" "$TMPDIR/before.json"

  run "$REPO_ROOT/bin/local/generate-public-surface" --check --quiet --out-dir "$TMPDIR"
  [ "$status" -eq 1 ]

  run cmp "$TMPDIR/public-surface.json" "$TMPDIR/before.json"
  [ "$status" -eq 0 ]
}

@test "generation is deterministic regardless of the caller's locale" {
  local dir_c="$TMPDIR/c" dir_en="$TMPDIR/en"
  env LC_ALL=C "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$dir_c" --quiet
  env LC_ALL=en_US.UTF-8 "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$dir_en" --quiet

  run cmp "$dir_c/public-surface.json" "$dir_en/public-surface.json"
  [ "$status" -eq 0 ]
  run cmp "$dir_c/ai/claude/public-surface.json" "$dir_en/ai/claude/public-surface.json"
  [ "$status" -eq 0 ]
}

@test "ai/serena tools land in the root snapshot, not ai/claude" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  run jq -e '.entries | index("command:serena-mcp")' "$TMPDIR/public-surface.json"
  [ "$status" -eq 0 ]
  run jq -e '.entries | index("command:serena-mcp")' "$TMPDIR/ai/claude/public-surface.json"
  [ "$status" -eq 1 ]
}

@test "every ai/claude registry tool has a matching command entry" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  while IFS= read -r name; do
    run jq -e --arg e "command:$name" '.entries | index($e)' "$TMPDIR/ai/claude/public-surface.json"
    [ "$status" -eq 0 ]
  done < <(yq -r '.tools[].name' "$REPO_ROOT/ai/claude/registry.yml")
}

@test "every ai/claude agent has a matching agent entry" {
  "$REPO_ROOT/bin/local/generate-public-surface" --out-dir "$TMPDIR" --quiet
  for f in "$REPO_ROOT"/ai/claude/agents/*.md; do
    name="$(basename "$f" .md)"
    run jq -e --arg e "agent:$name" '.entries | index($e)' "$TMPDIR/ai/claude/public-surface.json"
    [ "$status" -eq 0 ]
  done
}

@test "a broken jq call aborts instead of writing a truncated snapshot" {
  # GENERATOR_UNDER_TEST lets this same test run against an older copy of the
  # generator (see task-2-report.md) to prove it actually discriminates the
  # bug it targets, rather than passing against any implementation.
  local gen="${GENERATOR_UNDER_TEST:-$REPO_ROOT/bin/local/generate-public-surface}"
  local fakebin="$TMPDIR/fakebin" real_jq
  mkdir -p "$fakebin"
  real_jq="$(command -v jq)"
  # Fails only the _config_entries "props" jq call (matched by a fragment of
  # its jq program) and passes every other jq invocation through to the real
  # binary — this reproduces one broken pipeline stage among several, not a
  # total tool outage, since a total outage is already caught by _collect's
  # empty-category check and would not discriminate this bug.
  cat > "$fakebin/jq" <<EOF
#!/usr/bin/env bash
for a in "\$@"; do
  if [[ "\$a" == *"def props("* ]]; then
    echo "jq: 1 compile error" >&2
    exit 1
  fi
done
exec "$real_jq" "\$@"
EOF
  chmod +x "$fakebin/jq"

  run env PATH="$fakebin:$PATH" "$gen" --out-dir "$TMPDIR/out" --quiet
  [ "$status" -ne 0 ]
  [ ! -e "$TMPDIR/out" ]
  # Exit 1 alone is not enough: --check uses 1 for "the snapshot is stale",
  # which sends the contributor to this same script to fix a broken jq by
  # re-running it. The abort has to name the category that failed.
  [[ "$output" == *"config entries failed"* ]]
}

@test "a broken yq call inside _registries_for aborts instead of truncating" {
  # GENERATOR_UNDER_TEST — see the note on the jq fault-injection test above.
  local gen="${GENERATOR_UNDER_TEST:-$REPO_ROOT/bin/local/generate-public-surface}"
  local fakebin="$TMPDIR/fakebin" real_yq
  mkdir -p "$fakebin"
  real_yq="$(command -v yq)"
  # _registries_for's own yq calls are reached through a process substitution
  # (`done < <(_registries_for ...)` in _commands), which has no exit-status
  # channel back to the reader at all — pipefail and inherit_errexit both
  # only reach pipes and command substitutions, neither of which this is.
  # Failing yq only for one specific registry (not every call) reproduces a
  # single bad file, not a total tool outage, since a total outage would
  # make _registries_for itself fail every iteration in a way _collect's
  # empty-category check already catches.
  cat > "$fakebin/yq" <<EOF
#!/usr/bin/env bash
for a in "\$@"; do
  if [[ "\$a" == *"git/bin/registry.yml"* ]]; then
    echo "yq: fake failure on git/bin/registry.yml" >&2
    exit 1
  fi
done
exec "$real_yq" "\$@"
EOF
  chmod +x "$fakebin/yq"

  run env PATH="$fakebin:$PATH" "$gen" --out-dir "$TMPDIR/out" --quiet
  [ "$status" -ne 0 ]
  [ ! -e "$TMPDIR/out" ]
  # See the note on the jq test above — a silent exit 1 reads as a stale
  # snapshot, and re-running the generator "to fix it" exits 1 again.
  [[ "$output" == *"_registries_for failed for otto-workbench"* ]]
}

@test "a broken jq call inside _write_snapshot's render pipeline aborts instead of writing an empty snapshot" {
  # GENERATOR_UNDER_TEST — see the note on the jq fault-injection test above.
  local gen="${GENERATOR_UNDER_TEST:-$REPO_ROOT/bin/local/generate-public-surface}"
  local fakebin="$TMPDIR/fakebin" real_jq
  mkdir -p "$fakebin"
  real_jq="$(command -v jq)"
  # _write_snapshot's own render pipeline (printf | sort | jq -R | jq -s) is
  # only reachable if _write_snapshot itself runs under errexit, which
  # requires it not be called on the left of || at its call sites. Failing
  # only the `select(length > 0)` stage (unique to _write_snapshot, not used
  # by any category function) isolates this specific pipeline.
  cat > "$fakebin/jq" <<EOF
#!/usr/bin/env bash
for a in "\$@"; do
  if [[ "\$a" == *"select(length > 0)"* ]]; then
    echo "jq: fake failure on select(length > 0)" >&2
    exit 1
  fi
done
exec "$real_jq" "\$@"
EOF
  chmod +x "$fakebin/jq"

  run env PATH="$fakebin:$PATH" "$gen" --out-dir "$TMPDIR/out" --quiet
  [ "$status" -ne 0 ]
  [ ! -e "$TMPDIR/out/public-surface.json" ]
  [ ! -e "$TMPDIR/out/ai/claude/public-surface.json" ]
}
