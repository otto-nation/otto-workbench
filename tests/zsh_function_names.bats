#!/usr/bin/env bats
# Tests the naming rule for zsh functions the workbench defines: a function
# still defined once the snippets have loaded must not begin with a single
# underscore.
#
# Claude Code runs its Bash tool from a snapshot of the login shell's
# functions, and the snapshot leaves out every single-underscore name — zsh's
# completion-function convention. A wrapper the snapshot does capture fails
# there with "command not found" when it calls one. A single-underscore name is
# therefore only for a load-time helper that its own file removes with
# `unfunction` once it has run.
#
# A name the workbench does not own — a hook a third-party tool registers and
# the file overrides — takes a marker on the line immediately above it, in the
# grammar `ceiling:` uses:
#
#   # snapshot-exempt: mise activate registers this name as its precmd hook
#
# A marker with nothing after the colon exempts nothing.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# _single_underscore_functions FILE — the single-underscore function names FILE
# defines, in either `name()` or `function name` form, leaving out any carrying
# a `# snapshot-exempt: <reason>` marker on the line above.
_single_underscore_functions() {
  awk '
    /^[[:space:]]*# snapshot-exempt:[[:space:]]*[^[:space:]]/ { exempt = 1; next }
    match($0, /^[[:space:]]*(function[[:space:]]+_[A-Za-z][A-Za-z0-9_]*|_[A-Za-z][A-Za-z0-9_]*[[:space:]]*\(\))/) {
      name = substr($0, RSTART, RLENGTH)
      sub(/^[[:space:]]*(function[[:space:]]+)?/, "", name)
      sub(/[[:space:]]*\(\)$/, "", name)
      if (!exempt) print name
    }
    { exempt = 0 }
  ' "$1"
}

# _unfunctions FILE NAME — whether FILE removes NAME with `unfunction`.
_unfunctions() {
  grep -Eq "^[[:space:]]*unfunction([[:space:]]+[^[:space:]#]+)*[[:space:]]+$2([[:space:]]|\$)" "$1"
}

@test "every single-underscore zsh function is removed by the file that defines it" {
  local listing
  listing="$(find "$REPO_ROOT/zsh" -type f \( -name '*.zsh' -o -name '.zshrc' \) | sort)"
  [ -n "$listing" ]

  local -a offenders=()
  local file name
  while IFS= read -r file; do
    while IFS= read -r name; do
      [[ -n "$name" ]] || continue
      _unfunctions "$file" "$name" || offenders+=("${file#"$REPO_ROOT"/}: $name")
    done <<< "$(_single_underscore_functions "$file")"
  done <<< "$listing"

  if (( ${#offenders[@]} > 0 )); then
    printf 'outlives loading under a single-underscore name: %s\n' "${offenders[@]}"
    return 1
  fi
}

@test "the scan sees both definition forms, both unfunction placements, and the marker" {
  # The rule above is only as good as its matchers. Feed them a file holding
  # each shape so a pattern that stops matching one cannot pass silently.
  cat > "$TMPDIR/sample.zsh" <<'ZSH'
_paren_form() { :; }
function _keyword_form { :; }
__double_is_kept() { :; }
_removed_first() { :; }
_removed_last() { :; }
# snapshot-exempt: a third-party tool registers this name
function _exempt_hook() { :; }
# snapshot-exempt:
_bare_marker_exempts_nothing() { :; }
# snapshot-exempt: only the line directly above counts

_marker_too_far() { :; }
unfunction _removed_first other
unfunction other _removed_last 2>/dev/null
ZSH

  run _single_underscore_functions "$TMPDIR/sample.zsh"
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '%s\n' _paren_form _keyword_form _removed_first _removed_last \
    _bare_marker_exempts_nothing _marker_too_far)" ]

  _unfunctions "$TMPDIR/sample.zsh" _removed_first
  _unfunctions "$TMPDIR/sample.zsh" _removed_last
  run ! _unfunctions "$TMPDIR/sample.zsh" _paren_form
  run ! _unfunctions "$TMPDIR/sample.zsh" _removed
}
