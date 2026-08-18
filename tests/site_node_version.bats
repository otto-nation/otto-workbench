#!/usr/bin/env bats
# site/.node-version owns which Node the site builds on: ci.yml and pages.yml
# both consume it by reference through setup-node's node-version-file, and mise
# reads it locally. @types/node restates that same fact at compile time — its
# major IS a Node version, not an independent quality axis — so the two have to
# agree or the types describe a runtime we do not run.
#
# npm reports @types/node as outdated whenever any newer Node major exists,
# regardless of what we run, so "update everything to latest" walks straight
# into this. That already happened once. This is the cross-validation test
# CLAUDE.md prescribes when one set of defaults has to appear in more than one
# format; bumping the runtime is a one-line edit to .node-version, and this test
# then names the types as the thing that has to follow.

setup() {
  load 'test_helper'
  common_setup
  NODE_VERSION_FILE="$REPO_ROOT/site/.node-version"
  PACKAGE_JSON="$REPO_ROOT/site/package.json"
}

teardown() {
  common_teardown
}

# _runtime_major — the Node major site/.node-version pins.
_runtime_major() {
  tr -d '[:space:]' <"$NODE_VERSION_FILE" | cut -d. -f1
}

# _types_range — the raw @types/node range from site/package.json.
_types_range() {
  grep -oE '"@types/node": "[^"]*"' "$PACKAGE_JSON" | sed -E 's/.*: "(.*)"/\1/'
}

# _types_major — the Node major that range targets, range prefix stripped.
_types_major() {
  _types_range | sed -E 's/^[^0-9]*//' | cut -d. -f1
}

@test "site/.node-version pins a bare major" {
  local runtime
  runtime="$(_runtime_major)"
  [ -n "$runtime" ] || {
    echo "parsed no major out of $NODE_VERSION_FILE — fix the parser"
    return 1
  }
  [[ "$runtime" =~ ^[0-9]+$ ]] || {
    echo "site/.node-version should hold a major like '24', got '$runtime'"
    return 1
  }
}

@test "@types/node tracks the Node major site/.node-version owns" {
  local runtime types range
  runtime="$(_runtime_major)"
  types="$(_types_major)"
  range="$(_types_range)"
  [ -n "$types" ] || {
    echo "parsed no @types/node entry out of $PACKAGE_JSON — fix the parser"
    return 1
  }
  [ "$runtime" = "$types" ] || {
    echo "@types/node targets Node $types but site/.node-version pins $runtime"
    echo "  site/.node-version: $runtime"
    echo "  @types/node:        $range"
    echo "Types describe the runtime, so .node-version is the one to change first."
    return 1
  }
}
