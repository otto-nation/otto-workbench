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
# projects.sh guards on PROJECTS_REGISTRY_FILE, which only constants.sh
# defines, so load it when the caller has not. The eight gate scripts all
# source constants.sh before this file and pay nothing here; a caller that
# sources this module on its own — the test helper does — would otherwise die
# inside the guard. Same arrangement lib/registries.sh has with roots.sh, for
# the same reason.
if [[ -z "${PROJECTS_REGISTRY_FILE:-}" ]]; then
  # shellcheck source=../constants.sh
  . "$_session_count_lib_dir/constants.sh"
fi
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

# _encode_slug STRING CLASS — every character of STRING outside CLASS replaced
# by a hyphen. CLASS is the body of a glob bracket expression, so it must be
# passed unquoted at the `case` below: 'A-Za-z0-9' or 'A-Za-z0-9_'.
#
# A bash loop rather than `tr` or `sed`, because both halves of this transform
# have to land on the same name from two languages and three platforms:
#
#   - GNU `tr` is byte-oriented whatever the locale — the manual says outright
#     that it does not support multibyte characters — so `tr -c 'A-Za-z0-9'`
#     gives one hyphen per character on macOS and one per *byte* under Linux.
#     A 2-byte letter then slugs differently in CI than in a terminal, and no
#     locale pin fixes it.
#   - `sed` is per character in a UTF-8 locale on both, but BSD sed exits
#     non-zero on a byte that is not valid UTF-8 (`RE error: illegal byte
#     sequence`), which would abort a Stop-hook gate rather than slug the path.
#
# The loop is per character on bash 4.3+, the floor this repo requires, and
# turns an undecodable byte into one hyphen without failing. Python's halves in
# ai/lib/core/sessions.py iterate code points to match; tests/sessions_ssot.bats
# fails when the two drift.
#
# LC_ALL is local to this function: unpinned, ${#s} and ${s:i:1} count bytes
# and reproduce the GNU `tr` behaviour this exists to avoid. C.UTF-8 rather
# than en_US.UTF-8 because glibc has it built in and macOS carries it too.
# The class stays an ASCII range for the same reason it is not [[:alnum:]],
# which under this locale is Unicode-aware and would keep an accented letter.
_encode_slug() {
  local s="$1" class="$2" out="" i c
  local LC_ALL=C.UTF-8
  for ((i = 0; i < ${#s}; i++)); do
    c="${s:i:1}"
    case "$c" in
      [$class]) out+="$c" ;;
      *) out+="-" ;;
    esac
  done
  printf '%s' "$out"
}

# _canonical_slug PATH — the directory name standing for a project path.
#
# Pi's transform rather than Claude Code's: it keeps underscores, so
# `feat/add_auth` and `feat/add-auth` stay distinct where Claude's would collide
# them into one name. This names the gate stamps under $GATE_STAMPS_DIR — a
# repo's memory hangs off _claude_project_dir below, not off this.
# canonical_slug() in ai/lib/core/sessions.py is the same transform, and
# tests/sessions_ssot.bats fails when the two drift.
_canonical_slug() {
  local trimmed
  trimmed="${1#/}"
  trimmed="${trimmed%/}"
  printf -- '--%s--' "$(_encode_slug "$trimmed" 'A-Za-z0-9_')"
}

# _claude_project_dir DIR — the ~/.claude/projects directory holding Claude
# Code's transcripts for the session whose cwd is DIR.
#
# Claude names that directory for the absolute path of the session's cwd, with
# every character outside [A-Za-z0-9] replaced by a hyphen — a different
# transform from _canonical_slug, which is why both exist. This one addresses
# Claude's own store, which is also where a repo's memory lives; that one names
# the gate stamps. claude_slug in ai/lib/core/sessions.py is the Python half,
# held to this by tests/sessions_ssot.bats.
#
# ceiling: _encode_slug walks code points, while Claude's transform is a
# JavaScript regex and so walks UTF-16 code units. They agree across the BMP —
# `é` is one hyphen to both — and part ways on an astral-plane character, which
# is one hyphen here and two in the directory Claude actually created. Walking
# UTF-16 in bash would cost a fork this runs on every session exit to buy a cwd
# with an emoji in it. The transform is also what Claude Code does today rather
# than a documented contract. Upgrade to reading the directory off the hook
# payload if a session ever resolves to a directory that is not there.
_claude_project_dir() {
  printf '%s/projects/%s' "$CLAUDE_DIR" "$(_encode_slug "$1" 'A-Za-z0-9')"
}

# _claude_memory_dir DIR — where the memory for the repo at DIR lives.
#
# Memory is still kept in Claude's tree whichever harness a session ran in, and
# moving it out is its own change. Spelled here so the join has one owner:
# memory_dirs() in ai/lib/core/sessions.py is the Python half.
_claude_memory_dir() {
  printf '%s/memory' "$(_claude_project_dir "$1")"
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
  # Through _claude_project_dir rather than a second spelling of its `tr`: the
  # transform and its locale pin belong to one owner.
  claude_slug="$(basename "$(_claude_project_dir "$repo_dir")")"
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
    memory_dir="$(_claude_memory_dir "$repo_dir")"
    [[ -d "$memory_dir" ]] || continue
    printf '%s\t%s\n' "$memory_dir" "$repo_dir"
  done < <(project_repo_leaders)
  return 0
}

# _gate_stamp_file REPO_DIR NAME — where the per-repo gate cooldown NAME is
# recorded for REPO_DIR.
#
# Named by _canonical_slug under $GATE_STAMPS_DIR, so every worktree of a repo
# reads and writes one file and no harness owns it. The stamp used to live in
# the repo's Claude directory, which made the cooldown per worktree and left a
# Pi-only repo with nowhere to record one.
_gate_stamp_file() {
  printf '%s/%s.%s' "$GATE_STAMPS_DIR" "$(_canonical_slug "$1")" "$2"
}

# _gate_repo_dir DIR — the repo DIR belongs to, as the gates key their stamps
# and count their sessions by. A worktree resolves to the repository behind it,
# so every worktree of a repo shares one answer; a directory git cannot answer
# for is printed back unchanged.
#
# One `git rev-parse` per call, which is why the per-repo gate runs its stamp
# check before this rather than after.
_gate_repo_dir() {
  local dir="$1" shared
  shared="$(git_shared_dir "$dir")" || shared=""
  if [[ -z "$shared" ]]; then
    printf '%s' "$dir"
    return 0
  fi
  project_repo_label "$shared"
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
