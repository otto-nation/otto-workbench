#!/usr/bin/env bats
# Cross-validates the two definitions of a session (SSOT guard):
#   lib/ai/session-count.sh      — bash, for the Stop-hook gates
#   ai/lib/core/sessions.py      — Python, for everything downstream
#
# Two languages spell this because the gates run on every session exit and
# cannot afford a Python start-up. The bug class that buys is a gate and a
# scanner disagreeing about which sessions belong to a repo — the gate declines
# to fire while the scanner would have had plenty to read, and nothing reports
# it. Same arrangement, and same guard, as tests/workbench_roots.bats.

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
}

teardown() {
  common_teardown
}

# ─── Resolvers under test ───────────────────────────────────────────────────

# slug_shell PATH — the slug lib/ai/session-count.sh gives PATH.
slug_shell() {
  bash -c '. "$1/lib/ai/session-count.sh" 2>/dev/null; _canonical_slug "$2"' \
    _ "$REPO_ROOT" "$1"
}

# slug_python PATH — the slug ai/lib/core/sessions.py gives PATH.
slug_python() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from core import sessions
print(sessions.canonical_slug('$1'), end='')
"
}

# pi_slug_shell PATH — the Pi session directory name lib/ai/session-count.sh
# expects Pi to have given PATH.
pi_slug_shell() {
  bash -c '. "$1/lib/ai/session-count.sh" 2>/dev/null; _pi_session_slug "$2"' \
    _ "$REPO_ROOT" "$1"
}

# pi_slug_python PATH — the same, from ai/lib/core/sessions.py.
#
# Triple-quoted so a path holding a quote or a `$` does not break the generated
# source. The older helpers above interpolate bare and predate the Pi transform,
# which is the one that keeps such characters instead of hyphenating them away.
pi_slug_python() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from core import sessions
print(sessions.pi_session_slug('''$1'''), end='')
"
}

# count_python — interactive transcripts Python discovers under \$HOME.
count_python() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from pathlib import Path
from core import sessions
print(len(sessions.discover_sessions(Path('$HOME'))), end='')
"
}

# count_shell REPO — transcripts the shell side counts for REPO, all harnesses.
# Expressed as a count rather than a threshold so it can be compared directly
# against Python's; the gates use the thresholded form.
count_shell() {
  bash -c '
    . "$1/lib/ai/session-count.sh" 2>/dev/null
    total=0
    while IFS= read -r d; do
      [[ -n "$d" ]] || continue
      for f in "$d"*.jsonl; do [[ -f "$f" ]] && total=$((total + 1)); done
    done < <(_session_dirs_for_repo "$2")
    printf "%s" "$total"
  ' _ "$REPO_ROOT" "$1"
}

# make_session HARNESS_ROOT SLUG NAME — an empty transcript in a session dir.
make_session() {
  mkdir -p "$HOME/$1/$2"
  printf '{"type":"session","cwd":"/repo"}\n' > "$HOME/$1/$2/$3.jsonl"
}

# memdir_shell PATH — the memory directory lib/ai/session-count.sh gives PATH.
#
# constants.sh as well as the lib, which the resolvers above do not need:
# CLAUDE_DIR is named there, and session-count.sh deliberately does not source
# it — the gates that call it have already. Sourced under the test's own HOME so
# the path lands in the sandbox.
memdir_shell() {
  bash -c '. "$1/lib/constants.sh"; . "$1/lib/ai/session-count.sh" 2>/dev/null; _claude_memory_dir "$2"' \
    _ "$REPO_ROOT" "$1"
}

# memdir_python PATH — the memory directory ai/lib/core/sessions.py resolves
# PATH to. The forward transform, which the shell side spells too.
memdir_python() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from pathlib import Path
from core import sessions
print(sessions.claude_memory_dir(Path('$HOME'), '$1'), end='')
"
}

# memdirs_python — every memory directory ai/lib/core/sessions.py finds under
# \$HOME, one per line. The Python half sweeps rather than resolving forward:
# Claude's slug is lossy, so a directory name cannot say which repo it belongs
# to and only the shell side, which starts from the registry, can go that way.
memdirs_python() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from pathlib import Path
from core import sessions
for d in sessions.memory_dirs(Path('$HOME')):
    print(d)
"
}

# make_memory PATH — a memory directory for the repo at PATH, as the shell side
# names it. Returns the directory.
make_memory() {
  local dir
  dir="$(memdir_shell "$1")"
  mkdir -p "$dir"
  printf '%s' "$dir"
}

# ─── Slug transform ─────────────────────────────────────────────────────────

@test "both languages agree on the slug for a plain path" {
  local p="/Users/dev/git/project"
  [ "$(slug_shell "$p")" = "$(slug_python "$p")" ]
}

@test "both languages agree on the slug for a non-ASCII path" {
  # One hyphen per character, not per UTF-8 byte. Two ways these drift:
  # str.isalnum() is Unicode-aware and would keep an accented letter the shell
  # replaces, and GNU tr is byte-oriented whatever the locale, so the shell
  # half yielded `caf--` under CI while Python yielded `caf-`.
  #
  # The expected value is spelled out rather than only comparing the two,
  # because two byte-wise halves agree with each other and with nothing else —
  # including the directory Claude Code actually created.
  local p="/Users/dev/git/café/naïve"
  local want="--Users-dev-git-caf--na-ve--"
  [ "$(slug_shell "$p")" = "$want" ]
  [ "$(slug_python "$p")" = "$want" ]
}

@test "both languages preserve underscores in a slug" {
  local p="/Users/dev/git/repo/feat-add_auth"
  [ "$(slug_shell "$p")" = "$(slug_python "$p")" ]
  [[ "$(slug_shell "$p")" == *add_auth* ]]
}

@test "slugs keep hyphen and underscore branches distinct" {
  # Claude Code's transform collapses both to the same name; ours must not,
  # or two branches' memory lands in one directory.
  [ "$(slug_python /r/feat-a_b)" != "$(slug_python /r/feat-a-b)" ]
  [ "$(slug_shell  /r/feat-a_b)" != "$(slug_shell  /r/feat-a-b)" ]
}

@test "both languages agree on a path with dots and spaces" {
  local p="/Users/dev/my project/repo.git"
  [ "$(slug_shell "$p")" = "$(slug_python "$p")" ]
}

@test "trailing slashes do not change the slug" {
  [ "$(slug_shell /a/b/)" = "$(slug_shell /a/b)" ]
  [ "$(slug_python /a/b/)" = "$(slug_python /a/b)" ]
}

# ─── Pi's own transform ─────────────────────────────────────────────────────
#
# _canonical_slug names what this repo names itself; _pi_session_slug addresses
# the store Pi wrote. These pin the second against values Pi actually produces,
# which tests/pi_session_dir.bats checks against the real package.

@test "the Pi slug keeps a dot, where the canonical slug does not" {
  # The bug: a repo path holding a dot made its Pi sessions invisible to every
  # gate, because the canonical slug hyphenated what Pi had kept.
  local p="/Users/dev/git/otto.io"
  [ "$(pi_slug_shell "$p")" = "--Users-dev-git-otto.io--" ]
  [ "$(pi_slug_python "$p")" = "--Users-dev-git-otto.io--" ]
  [ "$(slug_shell "$p")" = "--Users-dev-git-otto-io--" ]
}

@test "the Pi slug keeps non-ASCII verbatim" {
  local p="/Users/dev/git/café/naïve" want="--Users-dev-git-café-naïve--"
  [ "$(pi_slug_shell "$p")" = "$want" ]
  [ "$(pi_slug_python "$p")" = "$want" ]
}

@test "the Pi slug keeps an astral character as one character" {
  # Pi's regex walks UTF-16 code units, but no surrogate holds the code unit for
  # `/`, `\` or `:`, so a code-point walk and a code-unit walk are the same
  # function here. That is why _pi_session_slug carries no UTF-16 ceiling and
  # _claude_project_dir does.
  local p="/Users/dev/git/🎉repo" want="--Users-dev-git-🎉repo--"
  [ "$(pi_slug_shell "$p")" = "$want" ]
  [ "$(pi_slug_python "$p")" = "$want" ]
}

@test "the Pi slug replaces backslash and colon" {
  [ "$(pi_slug_shell '/a/b:c')" = "--a-b-c--" ]
  [ "$(pi_slug_python '/a/b:c')" = "--a-b-c--" ]
  [ "$(pi_slug_shell '/a/b\c')" = "--a-b-c--" ]
  [ "$(pi_slug_python '/a/b\c')" = "--a-b-c--" ]
}

@test "the Pi slug survives a byte that is not valid UTF-8" {
  # BSD sed exits non-zero on one of these; a gate must slug the path rather
  # than abort on the way to deciding whether to fire.
  local p
  p=$'/a/\xff/b'
  run pi_slug_shell "$p"
  [ "$status" -eq 0 ]
  [ "$(printf '%s' "$output" | od -An -c | tr -d ' \n')" = "--a-377-b--" ]
}

@test "trailing and doubled slashes do not change the Pi slug" {
  [ "$(pi_slug_shell /a/b/)" = "$(pi_slug_shell /a/b)" ]
  [ "$(pi_slug_python /a/b/)" = "$(pi_slug_python /a/b)" ]
  [ "$(pi_slug_shell //a//b)" = "$(pi_slug_shell /a/b)" ]
  [ "$(pi_slug_python //a//b)" = "$(pi_slug_python /a/b)" ]
}

@test "a repo path holding a dot is found in Pi's store" {
  # The end-to-end form of the bug. Before the split this counted 0.
  make_session ".pi/agent/sessions" "--Users-dev-git-otto.io--" "a"
  make_session ".pi/agent/sessions" "--Users-dev-git-otto.io-feat_one--" "b"
  [ "$(count_shell /Users/dev/git/otto.io)" -eq 2 ]
}

@test "a repo whose Pi slug prefixes another is not confused with it" {
  # /a/b must not swallow /a/bc — the prefix arm needs a literal separator.
  make_session ".pi/agent/sessions" "--a-b--" "a"
  make_session ".pi/agent/sessions" "--a-bc--" "b"
  make_session ".pi/agent/sessions" "--a-b-feat--" "c"
  [ "$(count_shell /a/b)" -eq 2 ]
  [ "$(count_shell /a/bc)" -eq 1 ]
}

@test "a Pi slug carrying a glob metacharacter matches only itself" {
  # Pi keeps brackets where the canonical slug hyphenated them, so a slug can
  # now reach the unquoted pattern side of the prefix test as a live glob.
  make_session ".pi/agent/sessions" "--a-[xy]-b--" "a"
  make_session ".pi/agent/sessions" "--a-x-b--" "b"
  [ "$(count_shell '/a/[xy]/b')" -eq 1 ]
}

# ─── Discovery ──────────────────────────────────────────────────────────────

@test "both harnesses are discovered" {
  make_session ".claude/projects" "-repo" "one"
  make_session ".pi/agent/sessions" "--repo--" "two"
  [ "$(count_python)" -eq 2 ]
}

@test "pi's flat subagent transcripts are not sessions" {
  make_session ".pi/agent/sessions" "--repo--" "real"
  mkdir -p "$HOME/.pi/agent/sessions"
  printf '{}\n' > "$HOME/.pi/agent/sessions/subagent-123.jsonl"
  [ "$(count_python)" -eq 1 ]
}

@test "a missing harness root is not an error" {
  make_session ".claude/projects" "-repo" "one"
  [ ! -d "$HOME/.pi" ]
  [ "$(count_python)" -eq 1 ]
}

@test "no sessions anywhere counts zero" {
  [ "$(count_python)" -eq 0 ]
}

# ─── Repo scoping: the two languages must agree ─────────────────────────────

@test "both languages see every worktree's sessions as the repo's" {
  # The fragmentation this exists for: one repo, several worktrees, two
  # harnesses. A per-directory count would answer 1 for each of these.
  make_session ".claude/projects"   "-Users-dev-git-repo"           "a"
  make_session ".claude/projects"   "-Users-dev-git-repo-feat-one"  "b"
  make_session ".pi/agent/sessions" "--Users-dev-git-repo--"        "c"
  make_session ".pi/agent/sessions" "--Users-dev-git-repo-feat_two--" "d"

  [ "$(count_shell /Users/dev/git/repo)" -eq 4 ]
  [ "$(count_shell /Users/dev/git/repo)" = "$(count_python)" ]
}

@test "a different repo's sessions are not counted" {
  make_session ".claude/projects" "-Users-dev-git-repo"  "a"
  make_session ".claude/projects" "-Users-dev-git-other" "b"
  [ "$(count_shell /Users/dev/git/repo)" -eq 1 ]
}

@test "a repo whose name prefixes another is not confused with it" {
  # `repo` must not swallow `repo-tools`.
  make_session ".claude/projects" "-Users-dev-git-repo"       "a"
  make_session ".claude/projects" "-Users-dev-git-repo-tools" "b"
  # Both match by prefix, which is correct for worktrees but wrong here; the
  # count is the union, and the guard is that `repo-tools` alone sees only its own.
  [ "$(count_shell /Users/dev/git/repo-tools)" -eq 1 ]
}

@test "the repo-scoped gate counts across harnesses" {
  make_session ".claude/projects"   "-Users-dev-git-repo"    "a"
  make_session ".pi/agent/sessions" "--Users-dev-git-repo--" "b"
  run bash -c '
    . "$1/lib/ai/session-count.sh" 2>/dev/null
    _repo_has_enough_sessions "$2" 0 2
  ' _ "$REPO_ROOT" /Users/dev/git/repo
  [ "$status" -eq 0 ]
}

@test "the repo-scoped gate declines below the threshold" {
  make_session ".claude/projects" "-Users-dev-git-repo" "a"
  run bash -c '
    . "$1/lib/ai/session-count.sh" 2>/dev/null
    _repo_has_enough_sessions "$2" 0 2
  ' _ "$REPO_ROOT" /Users/dev/git/repo
  [ "$status" -eq 1 ]
}

# ─── Memory directory: the two languages must agree ─────────────────────────

@test "the directory the shell resolves is one Python sweeps up" {
  # The shell resolves a repo path forward to its memory directory; Python
  # globs every memory directory there is. A transform that drifts makes the
  # gate write a stamp into a directory no scanner reads.
  local dir
  dir="$(make_memory /Users/dev/git/repo)"
  [ "$(memdirs_python)" = "$dir" ]
}

@test "both languages agree on a repo path holding a dot and an underscore" {
  # Every character outside [A-Za-z0-9] becomes a hyphen, which a second
  # transform spelling only `/` gets wrong — that is what made the machine
  # profile report no memory for a repo whose files were on disk.
  local dir
  dir="$(make_memory /Users/dev/git/otto.io/feat_one)"
  [[ "$dir" == *-Users-dev-git-otto-io-feat-one/memory ]]
  [ "$(memdirs_python)" = "$dir" ]
}

@test "a project directory without memory is not swept up" {
  mkdir -p "$HOME/.claude/projects/-Users-dev-git-repo"
  [ -z "$(memdirs_python)" ]
}

@test "no projects root at all is not an error" {
  [ ! -d "$HOME/.claude/projects" ]
  [ -z "$(memdirs_python)" ]
}

@test "both languages agree on the memory directory for a non-ASCII repo path" {
  # As the slug case above, on the transform that has to find a directory
  # Claude Code created: one hyphen per character, so the suffix is pinned and
  # not merely compared across the two halves.
  local p="/Users/dev/git/café/naïve" got
  got="$(memdir_shell "$p")"
  [ "$got" = "$(memdir_python "$p")" ]
  [[ "$got" == *"/-Users-dev-git-caf--na-ve/memory" ]]
}

@test "both languages resolve a repo path to the same memory directory" {
  # The forward direction, which the gates use to find a repo's memory and the
  # architecture skill uses to read it. A drift here sends the two to different
  # directories for one repo.
  local p="/Users/dev/git/otto.io/feat_one"
  [ "$(memdir_shell "$p")" = "$(memdir_python "$p")" ]
}

@test "the forward resolver and the sweep meet at the same path" {
  local dir
  dir="$(make_memory /Users/dev/git/repo)"
  [ "$(memdir_python /Users/dev/git/repo)" = "$dir" ]
  [ "$(memdirs_python)" = "$dir" ]
}
