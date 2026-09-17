#!/usr/bin/env bash
# Session counting for the Stop-hook cooldown gates, and the project-directory
# helpers the completion scripts reset stamps with.
#
# The shell expression of ai/lib/core/sessions.py. Two languages spell this
# because the gates run on every session exit and cannot afford a Python
# start-up, while everything downstream of them is already Python. The pair is
# the same arrangement lib/roots.sh and ai/lib/core/workbench_paths.py have, and
# is held together the same way: tests/sessions_ssot.bats runs both against one
# fixture tree and fails when they disagree.
#
# Sourced directly rather than via lib/ui.sh: the Stop hooks that call this
# helper skip ui.sh to stay inside their startup budget.
_session_count_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../portable.sh
. "$_session_count_lib_dir/portable.sh"
# Which repos exist, for _memory_repos below. Also direct rather than via ui.sh,
# and after portable.sh because projects.sh pulls in git_layout.sh: the pair
# costs a few milliseconds of parsing and no forks, which is what keeps the
# gates inside the budget the comment above sets.
# shellcheck source=../projects.sh
. "$_session_count_lib_dir/projects.sh"
unset _session_count_lib_dir

# Session stores, one per harness, as paths under $HOME. Keep in step with
# HARNESSES in ai/lib/core/sessions.py.
#
# Only per-cwd directories inside these roots hold interactive transcripts. Pi
# also writes subagent runs as flat files at its root, which is why every walk
# below descends into directories rather than globbing the root: it drops those
# without having to name them.
SESSION_ROOTS=(".claude/projects" ".pi/agent/sessions")

# _has_enough_sessions PROJECT_DIR SINCE_TS MIN_COUNT
# Returns 0 if at least MIN_COUNT .jsonl files in PROJECT_DIR have mtime > SINCE_TS.
#
# Takes one directory: callers that mean "this repo" want _repo_has_enough_sessions
# below, which sweeps every directory belonging to a repo across both harnesses.
_has_enough_sessions() {
  local project_dir="$1" since="$2" min_count="$3"
  local count=0 session_file file_ts
  for session_file in "${project_dir}"*.jsonl; do
    [[ -f "$session_file" ]] || continue
    file_ts=$(file_mtime "$session_file") || file_ts=0
    if [[ "$file_ts" -gt "$since" ]]; then
      count=$((count + 1))
    fi
    if [[ "$count" -ge "$min_count" ]]; then
      return 0
    fi
  done
  return 1
}

# _canonical_slug PATH — the directory name standing for a project path.
#
# Pi's transform rather than Claude Code's: it keeps underscores, so
# `feat/add_auth` and `feat/add-auth` stay distinct where Claude's would collide
# them into one name. canonical_slug() in ai/lib/core/sessions.py is the same
# transform, and tests/sessions_ssot.bats fails when the two drift.
_canonical_slug() {
  local trimmed encoded
  trimmed="${1#/}"
  trimmed="${trimmed%/}"
  encoded="$(printf '%s' "$trimmed" | tr -c 'A-Za-z0-9_' '-')"
  printf -- '--%s--' "$encoded"
}

# _claude_project_dir DIR — the ~/.claude/projects directory holding Claude
# Code's transcripts for the session whose cwd is DIR.
#
# Claude names that directory for the absolute path of the session's cwd, with
# every character outside [A-Za-z0-9] replaced by a hyphen — a different
# transform from _canonical_slug, which is why both exist. This one addresses
# Claude's own store; that one names the harness-neutral directory a project's
# memory lives in.
#
# ceiling: the transform is what Claude Code does today and is not a documented
# contract. Upgrade to reading the directory off the hook payload if a session
# ever resolves to a directory that is not there.
_claude_project_dir() {
  local slug
  slug="$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '-')"
  printf '%s/projects/%s' "$CLAUDE_DIR" "$slug"
}

# _session_dirs_for_repo REPO_DIR — every harness directory holding sessions for
# the repo at REPO_DIR, one absolute path per line, each with a trailing slash.
#
# A repo is worked in from many cwds — one per worktree — and each gets its own
# session directory in each harness. maximum has 36 of them under Claude alone
# against a single memory directory, so a gate that counts one directory asks
# whether *this worktree* has been busy when the question is whether the repo
# has. Matching is by prefix on the encoded path, which is what makes a
# worktree's directory answer for the repo above it.
_session_dirs_for_repo() {
  local repo_dir="$1" root
  for root in "${SESSION_ROOTS[@]}"; do
    _repo_dirs_under_root "$HOME/$root" "$repo_dir"
  done
}

# _repo_dirs_under_root ROOT REPO_DIR — the session directories under one
# harness root belonging to REPO_DIR. Split out of _session_dirs_for_repo to
# keep both inside the two-level nesting limit.
_repo_dirs_under_root() {
  local abs_root="$1" repo_dir="$2" entry
  [[ -d "$abs_root" ]] || return 0
  for entry in "$abs_root"/*/; do
    if [[ -d "$entry" ]] && _dir_belongs_to_repo "$entry" "$repo_dir"; then
      printf '%s\n' "$entry"
    fi
  done
  return 0
}

# _dir_belongs_to_repo SESSION_DIR REPO_DIR — whether a session directory holds
# sessions run from REPO_DIR or a path beneath it.
#
# Compares encoded forms rather than decoding the directory name, because
# neither harness's encoding is reversible: Claude maps every non-alphanumeric to
# the same hyphen, so `a-b` and `a_b` both decode ambiguously. Encoding the repo
# path under both transforms and testing for a prefix is well defined in the
# direction that works.
_dir_belongs_to_repo() {
  local session_dir="$1" repo_dir="$2" name claude_slug pi_slug
  name="$(basename "$session_dir")"
  claude_slug="$(printf '%s' "$repo_dir" | tr -c 'A-Za-z0-9' '-')"
  pi_slug="$(_canonical_slug "$repo_dir")"
  pi_slug="${pi_slug%--}"

  [[ "$name" == "$claude_slug" || "$name" == "$claude_slug"-* ]] && return 0
  [[ "$name" == "$pi_slug--" || "$name" == "$pi_slug"-* ]] && return 0
  return 1
}

# _repo_has_enough_sessions REPO_DIR SINCE_TS MIN_COUNT — as
# _has_enough_sessions, but across every harness and every worktree of the repo.
_repo_has_enough_sessions() {
  local repo_dir="$1" since="$2" min_count="$3"
  local count=0 session_dir found
  while IFS= read -r session_dir; do
    [[ -n "$session_dir" ]] || continue
    found=$(_count_sessions_since "$session_dir" "$since")
    count=$((count + found))
    if [[ "$count" -ge "$min_count" ]]; then
      return 0
    fi
  done < <(_session_dirs_for_repo "$repo_dir")
  return 1
}

# _count_sessions_since SESSION_DIR SINCE_TS — how many transcripts in one
# directory are newer than SINCE_TS. Split out of _repo_has_enough_sessions to
# keep it inside the two-level nesting limit.
_count_sessions_since() {
  local session_dir="$1" since="$2"
  local count=0 session_file file_ts
  for session_file in "${session_dir}"*.jsonl; do
    [[ -f "$session_file" ]] || continue
    file_ts=$(file_mtime "$session_file") || file_ts=0
    [[ "$file_ts" -gt "$since" ]] || continue
    count=$((count + 1))
  done
  printf '%s' "$count"
}

# _memory_repos — every repo on this machine with a memory directory, one
# `<memory dir><TAB><repo dir>` line each.
#
# The sweep the three global gates share. It reads forward from the project
# registry — repo path, then encode to the directory its memory lives in —
# rather than globbing `$CLAUDE_DIR/projects/*/memory` and working back.
# Globbing yields slugs, and neither harness's slug is reversible: Claude maps
# `-` and `_` alike to a hyphen, so a directory name cannot say which repo it
# belongs to. A gate that starts from a slug therefore cannot ask the
# repo-scoped question at all, and is stuck counting the one directory it
# globbed — which is the bug being fixed here, not a step toward fixing it.
#
# A repo with no memory directory is skipped: there is nothing to consolidate
# and nowhere to record that a pass happened. A memory directory whose repo has
# left the registry is skipped too, and stops being swept — registration is an
# observation, so the recovery is to run any workbench command in that repo.
#
# Forks nothing once `record_project_repo_ids` has run, which the sync does:
# each line then already carries the repo identity, and only a registry line
# still missing one pays a `git rev-parse`.
_memory_repos() {
  # `worktree` is written by the nameref split and deliberately not read: the
  # leader is one checkout of the repo, and what a gate needs is the repo the
  # memory hangs off, which is what the identity resolves to.
  # shellcheck disable=SC2034
  local line id worktree repo_dir memory_dir
  while IFS= read -r line; do
    _split_repo_worktree_line "$line" id worktree
    repo_dir="$(project_repo_label "$id")"
    memory_dir="$(_claude_project_dir "$repo_dir")/memory"
    [[ -d "$memory_dir" ]] || continue
    printf '%s\t%s\n' "$memory_dir" "$repo_dir"
  done < <(project_repo_leaders)
  return 0
}

# _read_stamp FILE — the epoch seconds in FILE, or 0 when it is absent or
# unreadable. The three gates each read a cooldown stamp the same way.
_read_stamp() {
  local stamp_file="$1"
  [[ -f "$stamp_file" ]] || { printf '0'; return 0; }
  local value
  value="$(cat "$stamp_file" 2>/dev/null)" || value=""
  [[ "$value" =~ ^[0-9]+$ ]] || value=0
  printf '%s' "$value"
}
