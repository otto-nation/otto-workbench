#!/usr/bin/env bats
# Tests that every zsh function this repo writes still works from a Claude Code
# shell snapshot.
#
# Claude Code runs its Bash tool from a snapshot of the login shell's
# functions that leaves out every single-underscore name (claude_snapshot and
# CLAUDE_SNAPSHOT_DROPPED_RE in test_helper.bash). A function the snapshot
# keeps that calls one it drops fails there with "command not found". A
# single-underscore helper is fine as long as nothing kept calls it — a
# load-time helper, or a hook only zsh itself invokes.
#
# Scope: the functions written in this repo's zsh files. Functions a snippet
# creates by evaluating a third-party tool's output (`eval "$(mise activate
# zsh)"`) are that tool's code and are not read here.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# _zsh_sources — every zsh file the workbench ships, sorted.
_zsh_sources() {
  find "$REPO_ROOT" \( -name .git -o -name node_modules -o -name tests \) -prune \
    -o -type f \( -name '*.zsh' -o -name '.zshrc' \) -print | sort
}

# _function_calls FILE — one tab-separated line per function FILE defines,
# `def FILE NAME`, and per word in each function's body, `call FILE NAME WORD`.
# Bodies are tracked by brace depth, so one-line and multi-line definitions in
# either `name()` or `function name` form are read alike. Comments are dropped
# first, so a helper named in a comment is not a call.
_function_calls() {
  awk '
    function count(s, ch,   n, i) {
      n = 0
      for (i = 1; i <= length(s); i++) if (substr(s, i, 1) == ch) n++
      return n
    }
    {
      line = $0
      sub(/(^|[[:space:]])#.*/, "", line)
      if (depth == 0 && match(line, /^[[:space:]]*(function[[:space:]]+[^[:space:](){}]+([[:space:]]*\(\))?|[^[:space:](){}#=]+[[:space:]]*\(\))[[:space:]]*\{/)) {
        name = substr(line, RSTART, RLENGTH)
        sub(/^[[:space:]]*(function[[:space:]]+)?/, "", name)
        sub(/[[:space:]]*(\(\))?[[:space:]]*\{$/, "", name)
        print "def\t" FILENAME "\t" name
        current = name
        line = substr(line, RSTART + RLENGTH)
        depth = 1
      }
      if (depth > 0) {
        n = split(line, words, /[^A-Za-z0-9_:.-]+/)
        for (i = 1; i <= n; i++) if (words[i] != "") print "call\t" FILENAME "\t" current "\t" words[i]
        depth += count(line, "{") - count(line, "}")
        if (depth <= 0) depth = 0
      }
    }
  ' "$1"
}

# _snapshot_breaks SCAN — "FILE: KEPT calls DROPPED" for every function the
# snapshot keeps whose body calls a function this scan saw defined under a
# name the snapshot drops.
_snapshot_breaks() {
  local scan="$1" kind file name word
  local -A dropped=()
  while IFS=$'\t' read -r kind file name word; do
    if [[ "$kind" == def && "$name" =~ $CLAUDE_SNAPSHOT_DROPPED_RE ]]; then
      dropped[$name]=1
    fi
  done <<< "$scan"

  while IFS=$'\t' read -r kind file name word; do
    [[ "$kind" == call ]] || continue
    [[ "$name" =~ $CLAUDE_SNAPSHOT_DROPPED_RE ]] && continue
    if [[ -n "${dropped[$word]:-}" ]]; then
      printf '%s: %s calls %s\n' "${file#"$REPO_ROOT"/}" "$name" "$word"
    fi
  done <<< "$scan"
}

@test "no function a Claude Code shell snapshot keeps calls one it drops" {
  local listing
  listing="$(_zsh_sources)"
  [ -n "$listing" ]

  # Captured per file with its status checked, so a parser failure is a red
  # test rather than an empty scan that finds nothing to object to.
  local scan="" out file
  while IFS= read -r file; do
    out="$(_function_calls "$file")" || { echo "scan failed: $file"; return 1; }
    scan+="$out"$'\n'
  done <<< "$listing"
  [[ "$scan" == *$'def\t'* ]]

  run _snapshot_breaks "$scan"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "the scan reads every definition form and flags only a kept caller" {
  # The rule above is only as good as its parser. Feed it each shape so a
  # pattern that stops matching one cannot pass silently.
  cat > "$TMPDIR/sample.zsh" <<'ZSH'
_helper() { :; }
_wb-dash:helper() { :; }
kept_one_line() { _helper "$@"; }
function kept_keyword {
  if true; then
    _wb-dash:helper
  fi
}
kept_comment_only() {
  # _helper is only mentioned here
  :
}
_hook() { _helper; }
_helper
ZSH

  run _function_calls "$TMPDIR/sample.zsh"
  [ "$status" -eq 0 ]
  local defs
  defs="$(printf '%s\n' "$output" | awk -F'\t' '$1 == "def" { print $3 }')"
  [ "$defs" = "$(printf '%s\n' _helper _wb-dash:helper kept_one_line kept_keyword kept_comment_only _hook)" ]

  run _snapshot_breaks "$output"
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '%s\n' \
    "$TMPDIR/sample.zsh: kept_one_line calls _helper" \
    "$TMPDIR/sample.zsh: kept_keyword calls _wb-dash:helper")" ]
}

@test "the nvm shims still load node from a shell snapshot" {
  # The shims are kept by the snapshot; their loader must be too, or the first
  # `node` in the Bash tool fails with "command not found".
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME/.nvm"
  cat > "$HOME/.nvm/nvm.sh" <<'SH'
node() { print -r -- "NODE_RAN $*"; }
SH

  run claude_snapshot "$REPO_ROOT/zsh/config.d/tools/nvm.zsh"
  [ "$status" -eq 0 ]
  printf '%s\n' "$output" > "$TMPDIR/snapshot.zsh"

  run zsh -fc "export NVM_DIR='$HOME/.nvm'; source '$TMPDIR/snapshot.zsh'; node hello"
  [ "$status" -eq 0 ]
  [ "$output" = "NODE_RAN hello" ]
}
