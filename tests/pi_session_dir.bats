#!/usr/bin/env bats
# Cross-validates _pi_session_slug against the directory Pi actually creates.
#
# tests/sessions_ssot.bats holds the bash and Python halves to each other, which
# is the wrong question asked alone: both halves agreed perfectly with each
# other and with nothing Pi had ever written, for every repo path holding a dot
# or a non-ASCII character. A repo like that was invisible to all four session
# gates, and the SSOT suite stayed green throughout.
#
# So this asks Pi's own getDefaultSessionDir() and compares against the
# directory it leaves on disk. If a Pi upgrade changes the encoding, every
# repo's gates go blind at once and nothing else in the suite would report it.

setup() {
  load 'test_helper'
  common_setup
  SESSION_MANAGER_JS="$(npm root -g 2> /dev/null)/@earendil-works/pi-coding-agent/dist/core/session-manager.js"
  # Under the test's own scratch, so getDefaultSessionDir's mkdir never reaches
  # the real ~/.pi.
  PI_AGENT="$TMPDIR/agent"
  # Spelled as a $REPO_ROOT path literal rather than assembled inside the
  # `bash -c` below, so select-tests maps this file to the library it exercises
  # and runs it when that library changes.
  SESSION_COUNT_SH="$REPO_ROOT/lib/ai/session-count.sh"
}

teardown() {
  common_teardown
}

# _require_pi — skips a test that has to ask Pi what it names a directory.
_require_pi() {
  [[ -f "$SESSION_MANAGER_JS" ]] \
    || bats_skip "pi not installed — nothing to ask about session directory names"
}

# _pi_creates CWD — the directory name Pi creates for a session whose cwd is
# CWD, read back off the filesystem rather than taken from the return value:
# what the gates walk is the directory, so the directory is what must match.
_pi_creates() {
  cat > "$TMPDIR/probe.mjs" << JS
import { getDefaultSessionDir } from "file://$SESSION_MANAGER_JS";
getDefaultSessionDir(process.argv[2], "$PI_AGENT");
JS
  # No `--` separator: node passes it through as an argument, so it would land
  # in argv[2] and Pi would slug the string "--" against the cwd instead.
  node "$TMPDIR/probe.mjs" "$1" || return 1

  local entry
  for entry in "$PI_AGENT"/sessions/*/; do
    entry="${entry%/}"
    printf '%s' "${entry##*/}"
    return 0
  done
  return 1
}

# _ours CWD — the name lib/ai/session-count.sh expects Pi to have used.
_ours() {
  bash -c '. "$1" 2>/dev/null; _pi_session_slug "$2"' \
    _ "$SESSION_COUNT_SH" "$1"
}

@test "a plain path lands on the directory pi creates" {
  _require_pi
  [ "$(_ours /Users/dev/git/repo)" = "$(_pi_creates /Users/dev/git/repo)" ]
}

@test "a path holding a dot lands on the directory pi creates" {
  _require_pi
  # The reported bug. The canonical slug answered --Users-dev-git-otto-io--,
  # and Pi had written --Users-dev-git-otto.io--.
  local p="/Users/dev/git/otto.io"
  [ "$(_ours "$p")" = "$(_pi_creates "$p")" ]
}

@test "a non-ASCII path lands on the directory pi creates" {
  _require_pi
  local p="/Users/dev/git/café/naïve"
  [ "$(_ours "$p")" = "$(_pi_creates "$p")" ]
}

@test "an astral-plane path lands on the directory pi creates" {
  _require_pi
  # Pi's regex walks UTF-16 code units and this walks code points. They agree
  # because no surrogate holds the code unit for `/`, `\` or `:` — asserted
  # here against the real package rather than argued from the spec.
  local p="/Users/dev/git/🎉repo"
  [ "$(_ours "$p")" = "$(_pi_creates "$p")" ]
}

@test "a path holding a space and an underscore lands on the directory pi creates" {
  _require_pi
  local p="/Users/dev/my project/repo_one"
  [ "$(_ours "$p")" = "$(_pi_creates "$p")" ]
}

@test "a path holding a colon lands on the directory pi creates" {
  _require_pi
  local p="/Users/dev/git/repo:mirror"
  [ "$(_ours "$p")" = "$(_pi_creates "$p")" ]
}

@test "the canonical slug is deliberately not the directory pi creates" {
  _require_pi
  # Guards the split itself: if someone points _gate_stamp_file back at Pi's
  # transform, or collapses the two functions into one again, this fails.
  local p="/Users/dev/git/otto.io" canonical
  canonical="$(bash -c '. "$1" 2>/dev/null; _canonical_slug "$2"' \
    _ "$SESSION_COUNT_SH" "$p")"
  [ "$canonical" != "$(_pi_creates "$p")" ]
}
