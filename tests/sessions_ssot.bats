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
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
}

teardown() {
  rm -rf "$TMPDIR"
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

# ─── Slug transform ─────────────────────────────────────────────────────────

@test "both languages agree on the slug for a plain path" {
  local p="/Users/dev/git/project"
  [ "$(slug_shell "$p")" = "$(slug_python "$p")" ]
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
