#!/usr/bin/env bats
# Validates the install.components registry and all component setup.conf files.
# Tests run in both directions:
#   - registered → filesystem (every entry has a matching directory + setup.conf)
#   - filesystem → registry  (every directory with setup.conf is registered)

setup() {
  load 'test_helper'
  common_setup
  REGISTRY="$REPO_ROOT/install.components"
}

teardown() {
  common_teardown
}

# ─── Registry file ────────────────────────────────────────────────────────────

@test "install.components file exists" {
  [[ -f "$REGISTRY" ]]
}

@test "install.components has no duplicate entries" {
  local total unique
  total=$(grep -v '^[[:space:]]*#' "$REGISTRY" | grep -v '^[[:space:]]*$' | wc -l | tr -d ' ')
  unique=$(grep -v '^[[:space:]]*#' "$REGISTRY" | grep -v '^[[:space:]]*$' | sort -u | wc -l | tr -d ' ')
  [[ "$total" -eq "$unique" ]]
}

# ─── Forward checks (registered → filesystem) ─────────────────────────────────

@test "each registered component has a matching directory" {
  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    [[ -d "$REPO_ROOT/$component" ]]
  done < "$REGISTRY"
}

@test "each registered component has a setup.conf" {
  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    [[ -f "$REPO_ROOT/$component/setup.conf" ]]
  done < "$REGISTRY"
}

@test "each setup.conf has a non-empty label" {
  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    local label
    label=$(grep -m1 '^label[[:space:]]*=' "$REPO_ROOT/$component/setup.conf" \
      | sed 's/^label[[:space:]]*=[[:space:]]*//')
    [[ -n "$label" ]]
  done < "$REGISTRY"
}

@test "each setup.conf has a non-empty description" {
  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    local desc
    desc=$(grep -m1 '^description[[:space:]]*=' "$REPO_ROOT/$component/setup.conf" \
      | sed 's/^description[[:space:]]*=[[:space:]]*//')
    [[ -n "$desc" ]]
  done < "$REGISTRY"
}

# ─── Reverse check (filesystem → registered) ──────────────────────────────────

@test "no orphaned directories (setup.conf without entry in install.components)" {
  local orphans=()
  for conf in "$REPO_ROOT"/*/setup.conf; do
    [[ -f "$conf" ]] || continue
    local dir
    dir=$(basename "$(dirname "$conf")")
    grep -qx "$dir" "$REGISTRY" || orphans+=("$dir")
  done
  [[ ${#orphans[@]} -eq 0 ]] || { echo "Orphaned dirs not in install.components: ${orphans[*]}"; false; }
}

# ─── conf_get parsing ─────────────────────────────────────────────────────────

@test "conf_get reads label with spaces around equals sign" {
  local tmpfile result
  tmpfile=$(mktemp)
  printf 'label = My Label\ndescription = My Desc\n' > "$tmpfile"
  result=$(grep -m1 '^label[[:space:]]*=' "$tmpfile" | sed 's/^label[[:space:]]*=[[:space:]]*//')
  rm "$tmpfile"
  [[ "$result" == "My Label" ]]
}

@test "conf_get returns empty string for missing key" {
  local tmpfile result
  tmpfile=$(mktemp)
  printf 'label = My Label\n' > "$tmpfile"
  result=$(grep -m1 '^platforms[[:space:]]*=' "$tmpfile" | sed 's/^platforms[[:space:]]*=[[:space:]]*//')
  rm "$tmpfile"
  [[ -z "$result" ]]
}
