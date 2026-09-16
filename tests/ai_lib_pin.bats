#!/usr/bin/env bats
# Tests for WORKBENCH_AI_LIB_DIR — the pin that lets an ai/bin entry point load
# ai/lib out of the checkout it was invoked for rather than out of main/.
#
# The counterpart to the WORKBENCH_LIB_DIR block in tests/taskfile_global.bats,
# and asserted the same way: unset changes nothing, a valid pin moves the
# resolution, and a pin that is not a checkout is refused by the missing path's
# name rather than by a later import failure.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # An inherited pin would answer for the tests that set none, and hide the
  # default path entirely. Every case below states its own.
  unset WORKBENCH_AI_LIB_DIR 2>/dev/null || true
}

teardown() {
  common_teardown
}

# _probe SCRIPT — run SCRIPT as __main__ and report what its header resolved.
#
# `runpy` rather than executing the script, because the resolution is the
# subject: the probe has to read `sys.path` and `sys.modules` after the header
# ran, and a subprocess takes both with it when it exits. The interpreter is
# started with -I so the caller's PYTHONPATH cannot supply a module the header
# was meant to place.
#
# Which file answered `core`, not `sys.path[0]`: several `ai/lib` modules insert
# `<root>/lib` of their own while they import, so position 0 afterwards belongs
# to whichever of them ran last and says nothing about where `ai/lib` came from.
# The whole path is reported for the entries the pin must not leave behind.
#
# SystemExit is expected — every entry point here is given --help — and is
# swallowed so the epilogue runs. A refusal exits 2 before the epilogue, which
# is why the refusal cases below invoke the scripts directly instead.
_probe() {
  python3 -I - "$1" <<'PY'
import runpy
import sys

script = sys.argv[1]
sys.argv = [script, "--help"]
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit:
    pass
core = sys.modules.get("core")
out = [
    f"CORE_FILE {getattr(core, '__file__', '')}",
    f"BIN_ON_PATH {any(p.rstrip('/').endswith('/bin') for p in sys.path)}",
    f"LIBDIR_LOADED {'_libdir' in sys.modules}",
    "SYSPATH " + ":".join(sys.path),
]
sys.stderr.write("\n".join(out) + "\n")
PY
}

# _mirror_with_legacy SCRIPT — a checkout-shaped directory holding SCRIPT and a
# copy of it with the stanza rewritten back to the bare insert it replaced.
# Echoes the mirror's path; the two copies are `ai/bin/<name>` and
# `ai/bin/legacy-<name>`.
#
# "Unset behaves byte-identically" is a claim about a difference, so the test
# for it has to hold both sides. Asserting the resolved path against a literal
# would pass just as well if the stanza had changed it to something else that
# also looked right; running the pre-change form is the only form of the
# assertion that can fail for the reason it exists.
#
# Both copies live under one root because each resolves `ai/lib` from its own
# `__file__`. Run from different directories they would report different paths
# for no reason the test is about, and the comparison would have to be loosened
# to the point of proving nothing.
_mirror_with_legacy() {
  local script="$1"
  local mirror="$BATS_TEST_TMPDIR/mirror"
  local name
  name=$(basename "$script")
  mkdir -p "$mirror/ai/bin"
  # Resolved, because the scripts resolve their own `__file__` and BATS_TEST_TMPDIR
  # is reached through a symlink on macOS — an unresolved path would name a
  # directory the probe never reports.
  mirror=$(cd "$mirror" && pwd -P)
  ln -s "$REPO_ROOT/ai/lib" "$mirror/ai/lib"
  ln -s "$REPO_ROOT/lib" "$mirror/lib"
  cp "$script" "$mirror/ai/bin/$name"
  cp "$REPO_ROOT/ai/bin/_libdir.py" "$mirror/ai/bin/"
  python3 - "$script" "$mirror/ai/bin/legacy-$name" <<'PY'
import re
import sys

source, dest = sys.argv[1], sys.argv[2]
text = open(source).read()
stanza = re.compile(
    r'# ai/lib, from WORKBENCH_AI_LIB_DIR.*?\nsys\.path\.insert\(0, str\(_AI_LIB_DIR\)\)\n',
    re.S)
text, n = stanza.subn(
    'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))\n', text)
if n != 1:
    sys.exit(f"expected one stanza in {source}, found {n}")
open(dest, "w").write(text.replace("import os\nimport sys", "import sys"))
PY
  printf '%s' "$mirror"
}

# ─── Unset — the default path, unchanged ─────────────────────────────────────

@test "an unset pin resolves ai/lib exactly as the bare insert did" {
  # The constraint read strictly, against the code it replaced rather than
  # against a literal: the same sys.path, entry for entry and in order.
  local mirror before after
  mirror=$(_mirror_with_legacy "$REPO_ROOT/ai/bin/pr-describe")

  run -0 _probe "$mirror/ai/bin/legacy-pr-describe"
  before="$output"
  run -0 _probe "$mirror/ai/bin/pr-describe"
  after="$output"

  [ "$before" = "$after" ]
  # And named, so a failure says which property went: no module newly
  # importable out of ai/bin, and the helper not even loaded.
  [[ "$after" == *"BIN_ON_PATH False"* ]]
  [[ "$after" == *"LIBDIR_LOADED False"* ]]
  [[ "$after" == *"CORE_FILE $mirror/ai/lib/core/"* ]]
}

@test "the probe distinguishes the two trees" {
  # Vacuity guard for the test above: a probe whose epilogue never ran, or that
  # reported a constant, would pass it having compared nothing. The same probe
  # under a pin must report a different tree.
  local pin
  pin=$(make_fake_ai_pin "$REPO_ROOT")

  WORKBENCH_AI_LIB_DIR="$pin" run -0 _probe "$REPO_ROOT/ai/bin/pr-describe"
  [[ "$output" == *"CORE_FILE $pin/ai/lib/core/"* ]]
  [[ "$output" != *"CORE_FILE $REPO_ROOT/ai/lib/core/"* ]]
}

@test "an empty pin reads as unset" {
  # A variable exported to nothing is nobody asking for a pin, and it must not
  # be refused as a relative path.
  WORKBENCH_AI_LIB_DIR="" run -0 _probe "$REPO_ROOT/ai/bin/pr-describe"
  [[ "$output" == *"CORE_FILE $REPO_ROOT/ai/lib/core/"* ]]
  [[ "$output" == *"LIBDIR_LOADED False"* ]]
}

# ─── Set — the named tree answers ────────────────────────────────────────────

@test "a valid pin loads ai/lib out of the named checkout" {
  local pin
  pin=$(make_fake_ai_pin "$REPO_ROOT")

  WORKBENCH_AI_LIB_DIR="$pin" run -0 _probe "$REPO_ROOT/ai/bin/pr-describe"
  # A module actually imported from the pin, not merely a path entry naming it:
  # a pin that reached sys.path while something else answered the import is the
  # failure this distinguishes.
  [[ "$output" == *"CORE_FILE $pin/ai/lib/core/"* ]]
  [[ "$output" == *"SYSPATH "*"$pin/ai/lib"* ]]
  # And the bootstrap window closed again: ai/bin is reached to import _libdir
  # and removed before anything else resolves.
  [[ "$output" == *"BIN_ON_PATH False"* ]]
}

@test "_version.py does not shadow the pin" {
  # wiki, retro-scan, dream-scan and promote-scan import _version after placing
  # ai/lib, and _version's own body places an ai/lib of its own. Without the
  # stanza in _version.py that insert lands in front of the pinned path and
  # silently answers every import after it.
  local pin
  pin=$(make_fake_ai_pin "$REPO_ROOT")

  WORKBENCH_AI_LIB_DIR="$pin" run -0 _probe "$REPO_ROOT/ai/bin/wiki"
  [[ "$output" == *"CORE_FILE $pin/ai/lib/core/"* ]]
}

# ─── Refused — a pin that is not a checkout ──────────────────────────────────

@test "a relative pin is refused before anything loads" {
  WORKBENCH_AI_LIB_DIR="relative/path" run "$REPO_ROOT/ai/bin/pr-describe" --help
  [ "$status" -eq 2 ]
  [[ "$output" == *"WORKBENCH_AI_LIB_DIR must be an absolute path: relative/path"* ]]
  # A misspelled environment variable is not diagnosed by a stack trace.
  [[ "$output" != *"Traceback"* ]]
}

@test "a pin that is not a checkout is refused, naming the missing witness" {
  mkdir -p "$BATS_TEST_TMPDIR/empty"

  WORKBENCH_AI_LIB_DIR="$BATS_TEST_TMPDIR/empty" run "$REPO_ROOT/ai/bin/pr-describe" --help
  [ "$status" -eq 2 ]
  [[ "$output" == *"does not contain ai/lib/core/__init__.py"* ]]
  [[ "$output" == *"$BATS_TEST_TMPDIR/empty"* ]]
  [[ "$output" != *"Traceback"* ]]
}

@test "a pin with ai/ but no lib/ is refused" {
  # The witness list is not merely "does ai/lib exist". ai/lib reaches up to
  # <root>/lib for git_remote, git_layout and nesting, so a root supplying only
  # ai/ is a build no tree has — and it fails several frames into a push rather
  # than here unless the pin is checked for it.
  local partial="$BATS_TEST_TMPDIR/partial"
  mkdir -p "$partial"
  ln -s "$REPO_ROOT/ai" "$partial/ai"

  WORKBENCH_AI_LIB_DIR="$partial" run "$REPO_ROOT/ai/bin/pr-describe" --help
  [ "$status" -eq 2 ]
  [[ "$output" == *"does not contain lib/git_remote.py"* ]]
}

@test "the refusal reaches every entry point, not just the one under test" {
  # The stanza is duplicated by design, so a file that missed it fails open:
  # it would run against main's ai/lib while its siblings honoured the pin.
  local script
  for script in pr pr-describe ci-check wiki otto-log review-threads claude-review; do
    WORKBENCH_AI_LIB_DIR=/nonexistent run "$REPO_ROOT/ai/bin/$script" --help
    [ "$status" -eq 2 ]
    [[ "$output" == *"WORKBENCH_AI_LIB_DIR"* ]]
  done
}

# ─── The stanza itself ───────────────────────────────────────────────────────

@test "every entry point that places ai/lib goes through the pin" {
  # The counterpart to taskfile_global.bats's "every lib/ai source line goes
  # through \$WORKBENCH_LIB_DIR". A new script writing the bare insert pins
  # itself to the installed checkout while its siblings follow the pin, and
  # nothing else reports that.
  local bad
  bad=$(grep -rln 'sys.path.insert(0, str(.*parent.* / "lib"))' \
      "$REPO_ROOT/ai/bin" "$REPO_ROOT/ai/claude/bin" \
    | xargs grep -L 'WORKBENCH_AI_LIB_DIR') || true
  [ -z "$bad" ]
}

@test "every carrier of the stanza spells it identically" {
  # Duplication is the design, so identicality is the property under test: the
  # five lines below the _AI_LIB_DIR binding must be one text everywhere. Only
  # that binding varies, because ai/claude/bin sits one directory deeper.
  local carriers count shapes
  # --include, because a __pycache__ left by an earlier run holds the variable's
  # name in a compiled header and would join the comparison as a shape of its
  # own. The tarball build script names the variable in a comment and is not a
  # carrier either.
  carriers=$(grep -rl --include='*' --exclude-dir=__pycache__ \
      'os.environ.get("WORKBENCH_AI_LIB_DIR")' \
      "$REPO_ROOT/ai/bin" "$REPO_ROOT/ai/claude/bin" \
    | grep -v '_libdir.py' | sort)
  count=$(printf '%s\n' "$carriers" | grep -c .)
  # Vacuity: a grep that stopped selecting would pass this having compared one
  # file against itself, or none against nothing.
  [ "$count" -ge 21 ]

  shapes=$(while IFS= read -r f; do
      grep -A4 '^if os.environ.get("WORKBENCH_AI_LIB_DIR"):$' "$f" | md5
    done <<< "$carriers" | sort -u | grep -c .)
  [ "$shapes" -eq 1 ]
}
