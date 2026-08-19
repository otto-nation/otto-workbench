#!/usr/bin/env bash
# The repos on this machine that use otto-workbench.
#
# Membership means a workbench command actually ran in a repo, not that a
# `.claude` directory turned up somewhere under a directory someone guessed at.
# Both consumers that used to guess — the machine profile generator and the
# project-scoped migrations — read this list instead of deriving their own.
#
# The file is one absolute path per line under the state root, with `#` comment
# lines. Text rather than YAML for the reason `migrations.applied` is: every
# write is an append and every read is a scan, and YAML would pay a `yq` fork on
# each of them.
#
# ai/lib/workbench_projects.py is the Python half — Claude's SessionStart hook
# and the `pr` CLI register through it, and it reads and writes the same file in
# the same shape. tests/projects.bats cross-validates the two.
#
# The filename itself is declared once, in lib/constants.sh, and this file holds
# functions only.

# Guard: constants must be loaded (provides PROJECTS_REGISTRY_FILE, plus the
# state and cache roots the exclusion rules below refer to)
if [[ -z "${PROJECTS_REGISTRY_FILE:-}" ]]; then
  echo "ERROR: lib/projects.sh requires PROJECTS_REGISTRY_FILE (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

# The line the backfill leaves behind so it runs exactly once per machine.
#
# A marker inside the file rather than the file's own existence: the Python half
# creates the file the first time a `pr` invocation registers a repo, and a
# backfill keyed on existence would then be skipped forever on a machine that
# used a tool before it next synced.
_PROJECTS_SEED_MARKER="# backfilled from"

# ─── Membership rules ────────────────────────────────────────────────────────

# The path prefixes nothing under is ever registered.
#
# Test harnesses are the reason this exists: `bats` builds throwaway git repos
# under $TMPDIR, and the validators and pre-commit hooks that run inside them
# are workbench commands by every other test. Registering those fills the file
# with directories that are gone by the time anything reads it. The workbench's
# own state and cache roots are here for the same reason — the review system
# builds worktrees under them. ai/lib/workbench_projects.py spells the same set.
#
# Assignable, so a test can register a repo it built in a temp directory — which
# is the only kind of repo a test is allowed to build.
# The /private twins are not redundant: /tmp and /var/folders are symlinks into
# /private on macOS, and every caller resolves its path through `git rev-parse
# --show-toplevel`, which hands back the realpath.
#
# The state and cache roots get a resolved spelling added too, since those come
# from env vars a caller may well have written with a symlink in them —
# `ai/lib/workbench_projects.py`'s `excluded()` does the same. This is a one-time
# fork at array-build time, not a per-comparison one.
if [[ -z "${PROJECTS_EXCLUDED_PREFIXES+x}" ]]; then
  PROJECTS_EXCLUDED_PREFIXES=(
    "${TMPDIR:-}" /tmp /private/tmp /var/folders /private/var/folders
    "${WORKBENCH_STATE_DIR:-}" "${WORKBENCH_CACHE_DIR:-}"
  )
  if [[ -n "${WORKBENCH_STATE_DIR:-}" && -d "${WORKBENCH_STATE_DIR}" ]]; then
    PROJECTS_EXCLUDED_PREFIXES+=("$(cd "$WORKBENCH_STATE_DIR" && pwd -P)")
  fi
  if [[ -n "${WORKBENCH_CACHE_DIR:-}" && -d "${WORKBENCH_CACHE_DIR}" ]]; then
    PROJECTS_EXCLUDED_PREFIXES+=("$(cd "$WORKBENCH_CACHE_DIR" && pwd -P)")
  fi

fi

# _project_excluded DIR — true when DIR must never enter the registry.
_project_excluded() {
  local dir="${1%/}" prefix
  if [[ "$dir" != /* ]]; then
    return 0
  fi
  # `:-` so an empty list expands to one empty string rather than tripping
  # `set -u` in the callers that set it.
  for prefix in "${PROJECTS_EXCLUDED_PREFIXES[@]:-}"; do
    prefix="${prefix%/}"
    if [[ -n "$prefix" && ( "$dir" == "$prefix" || "$dir" == "$prefix"/* ) ]]; then
      return 0
    fi
  done
  return 1
}

# _project_is_worktree DIR — true when DIR is the root of a git work tree.
#
# `.git` alone is not the test. A bare-repo layout — what `wt-init` produces and
# what the workbench itself lives in — puts a bare repository at
# <container>/.git, and the container is not a repo: `git rev-parse
# --show-toplevel` refuses to run there. Registering it would put a directory in
# the list that holds worktrees rather than being one.
_project_is_worktree() {
  local dir="$1"
  if [[ ! -d "$dir" || ! -e "$dir/.git" ]]; then
    return 1
  fi
  if [[ ! -f "$dir/.git/config" ]]; then
    return 0
  fi
  ! grep -qE '^[[:space:]]*bare[[:space:]]*=[[:space:]]*true' "$dir/.git/config"
}

# ─── Reads and writes ────────────────────────────────────────────────────────

# _project_ensure_file — create the registry, and the state root it sits in.
_project_ensure_file() {
  # mkdir -p's own exit status is intentionally not checked here: a failure on
  # a state root that already exists from a prior run would still leave this
  # function reporting success, but the `: >` (or the caller's `>>`) below hits
  # the same permission failure and propagates it as return 2.
  mkdir -p "$(dirname "$PROJECTS_REGISTRY_FILE")"
  [[ -f "$PROJECTS_REGISTRY_FILE" ]] || : > "$PROJECTS_REGISTRY_FILE"
}

# _project_contains DIR — true when DIR already has a line in the registry.
_project_contains() {
  [[ -f "$PROJECTS_REGISTRY_FILE" ]] || return 1
  grep -qxF "${1%/}" "$PROJECTS_REGISTRY_FILE"
}

# project_register DIR — record DIR as a repo that uses the workbench.
#
# DIR must already be a resolved work-tree root — every caller has one in hand
# (`git rev-parse --show-toplevel` for the shell callers, `ctx.worktree_root`
# for `pr`), so this deliberately does no discovery of its own and forks
# nothing.
#
# Returns 0 when DIR is newly added to the registry, 3 when it was already
# there, 1 when the membership rules refused it, and 2 when it qualified but
# the registry could not be written. The callers that register as a side
# effect treat every non-zero the same; `otto-workbench projects add` is the
# one that has to say which happened, and "not a project" is the wrong thing
# to tell someone whose state root is read-only. Reporting the already-
# registered case as its own code lets that caller ask this function once
# instead of scanning the registry itself first to find out.
  mkdir -p "$(dirname "$PROJECTS_REGISTRY_FILE")"
  [[ -f "$PROJECTS_REGISTRY_FILE" ]] || : > "$PROJECTS_REGISTRY_FILE"
}

# _project_contains DIR — true when DIR already has a line in the registry.
_project_contains() {
  [[ -f "$PROJECTS_REGISTRY_FILE" ]] || return 1
  grep -qxF "${1%/}" "$PROJECTS_REGISTRY_FILE"
}

# project_register DIR — record DIR as a repo that uses the workbench.
#
# DIR must already be a resolved work-tree root — every caller has one in hand
# (`git rev-parse --show-toplevel` for the shell callers, `ctx.worktree_root`
# for `pr`), so this deliberately does no discovery of its own and forks
# nothing.
#
  # mkdir -p's own exit status is intentionally not checked here: a failure on
  # a state root that already exists from a prior run would still leave this
  # function reporting success, but the `: >` (or the caller's `>>`) below hits
  # the same permission failure and propagates it as return 2.
  mkdir -p "$(dirname "$PROJECTS_REGISTRY_FILE")"
  [[ -f "$PROJECTS_REGISTRY_FILE" ]] || : > "$PROJECTS_REGISTRY_FILE"
}

# _project_contains DIR — true when DIR already has a line in the registry.
_project_contains() {
  [[ -f "$PROJECTS_REGISTRY_FILE" ]] || return 1
  grep -qxF "${1%/}" "$PROJECTS_REGISTRY_FILE"
}

# project_register DIR — record DIR as a repo that uses the workbench.
#
# DIR must already be a resolved work-tree root — every caller has one in hand
# (`git rev-parse --show-toplevel` for the shell callers, `ctx.worktree_root`
# for `pr`), so this deliberately does no discovery of its own and forks
# nothing.
#
# Returns 0 when DIR is newly added to the registry, 3 when it was already
# there, 1 when the membership rules refused it, and 2 when it qualified but
# the registry could not be written. The callers that register as a side
# effect treat every non-zero the same; `otto-workbench projects add` is the
# one that has to say which happened, and "not a project" is the wrong thing
# to tell someone whose state root is read-only. Reporting the already-
# registered case as its own code lets that caller ask this function once
# instead of scanning the registry itself first to find out.
project_register() {
  local dir="${1%/}"
  if _project_excluded "$dir" || ! _project_is_worktree "$dir"; then
    return 1
  fi
  if _project_contains "$dir"; then
    return 3
project_register() {
  local dir="${1%/}"
  if _project_excluded "$dir" || ! _project_is_worktree "$dir"; then
    return 1
  fi
  if _project_contains "$dir"; then
# Returns 0 when DIR is newly added to the registry, 3 when it was already
# there, 1 when the membership rules refused it, and 2 when it qualified but
# the registry could not be written. The callers that register as a side
# effect treat every non-zero the same; `otto-workbench projects add` is the
# one that has to say which happened, and "not a project" is the wrong thing
# to tell someone whose state root is read-only. Reporting the already-
# registered case as its own code lets that caller ask this function once
# instead of scanning the registry itself first to find out.
project_register() {
  local dir="${1%/}"
  if _project_excluded "$dir" || ! _project_is_worktree "$dir"; then
    return 1
  fi
  if _project_contains "$dir"; then
    return 3
  fi
  _project_ensure_file || return 2
  printf '%s\n' "$dir" >> "$PROJECTS_REGISTRY_FILE" || return 2
}

# project_registered — print every registered repo that still exists, one per line.
#
# A directory that is gone is skipped rather than rewritten away: dropping stale
# entries at read time is what saves the registry from needing a pruning job,
# and a read is the wrong place to take a write lock. `otto-workbench projects
# prune` is what makes the drop permanent.
#
# Repeats are dropped here too. Registration is an append guarded by a
# membership check rather than a lock, so two workbench commands starting in the
# same repo at the same moment can each read "absent" and each append. Absorbing
# that on read is what a lock would buy, without making every hook pay for one.
project_registered() {
  [[ -f "$PROJECTS_REGISTRY_FILE" ]] || return 0
  local line
  local -A seen=()
  # `|| [[ -n "$line" ]]`: read reports EOF for a final line with no newline
  # after it, and the loop body would never see it.
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" || "$line" == \#* || -n "${seen[$line]:-}" ]]; then
      continue
    fi
    seen[$line]=1
    if [[ -d "$line" ]]; then
      printf '%s\n' "$line"
    fi
  done < "$PROJECTS_REGISTRY_FILE"
}

# _project_rewrite LINES... — replace the registry with exactly these lines.
#
# Built in a temp file and swapped in with `mv` so a process killed mid-write
# never leaves the registry truncated — `project_forget` and `project_prune`
# are explicit, user-invoked commands, and losing entries to a partial write
# there is a lot more surprising than during passive registration.
_project_rewrite() {
  local tmp mode
  tmp="$(mktemp "${PROJECTS_REGISTRY_FILE}.XXXXXX")" || return 1
  mode=$(file_mode "$PROJECTS_REGISTRY_FILE" 2>/dev/null) && chmod "$mode" "$tmp"
  if (( $# > 0 )); then
    printf '%s\n' "$@" > "$tmp" || { rm -f "$tmp"; return 1; }
  fi
  mv "$tmp" "$PROJECTS_REGISTRY_FILE"
}

# project_forget DIR — drop DIR's entry. Returns 1 when it had none.
project_forget() {
  local dir="${1%/}"
  if ! _project_contains "$dir"; then
    return 1
  fi
  local -a kept=()
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -n "$line" && "$line" != "$dir" ]]; then
      kept+=("$line")
    fi
  done < "$PROJECTS_REGISTRY_FILE"
  _project_rewrite "${kept[@]}"
}

# project_prune — drop entries whose directory is gone, and repeats. Prints how many went.
project_prune() {
  if [[ ! -f "$PROJECTS_REGISTRY_FILE" ]]; then
    echo 0
    return 0
  fi
  local line dropped=0
  local -a kept=()
  local -A seen=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" ]]; then
      continue
    fi
    if [[ "$line" == \#* ]]; then
      kept+=("$line")
      continue
    fi
    # A repeat is what project_registered was already absorbing on read; this is
    # where the absorbing stops being needed.
    if [[ -d "$line" && -z "${seen[$line]:-}" ]]; then
      seen[$line]=1
      kept+=("$line")
      continue
    fi
    dropped=$(( dropped + 1 ))
  done < "$PROJECTS_REGISTRY_FILE"

  if (( dropped > 0 )); then
    _project_rewrite "${kept[@]}"
  fi
  echo "$dropped"
}

# ─── One-time backfill ───────────────────────────────────────────────────────

# _project_seed_candidates — the directories Claude Code recorded sessions in.
#
# `.projects` in ~/.claude.json is keyed by absolute path and written by Claude
# itself, so it is an observation rather than another guess about where repos
# live. Each key is the cwd a session started in, which is not necessarily a
# work-tree root — _project_seed_roots is what turns one into roots.
_project_seed_candidates() {
  if [[ ! -f "$CLAUDE_CONFIG_FILE" ]]; then
    return 0
  fi
  jq -r '(.projects // {}) | keys[]' "$CLAUDE_CONFIG_FILE" 2>/dev/null || true
}

# _project_seed_roots CANDIDATE — the work-tree root a recorded cwd stands for.
#
# Usually whatever `git rev-parse --show-toplevel` reports. The case that needs
# more is a bare-repo container — the layout `wt-init` and `worktrunk` produce,
# and a directory Claude records whenever a session starts at the container
# rather than inside one of its worktrees. `--show-toplevel` refuses to run
# there, so resolving by that alone yielded nothing and left the entire repo out
# of the backfill.
#
# The container's HEAD names its default branch, and the worktree checked out on
# that branch is the one that stands for the repo — the same choice
# WORKBENCH_STABLE_DIR makes. Its feature worktrees are deliberately not seeded:
# they come and go, each one would be a row of its own everywhere the registry
# is read, and any that is still around registers itself the next time a
# workbench command runs in it.
_project_seed_roots() {
  local candidate="$1" root branch
  root="$(git -C "$candidate" rev-parse --show-toplevel 2>/dev/null)" || root=""
  if [[ -n "$root" ]]; then
    printf '%s\n' "$root"
    return 0
  fi

  branch="$(git --git-dir="$candidate/.git" symbolic-ref --short HEAD 2>/dev/null)" || return 0
  if [[ -z "$branch" ]]; then
    return 0
  fi
  git --git-dir="$candidate/.git" worktree list --porcelain 2>/dev/null | awk -v \
    want="branch refs/heads/$branch" '
      /^worktree /   { path = substr($0, 10) }
      $0 == want     { print path; exit }
    '
}

# seed_project_registry — backfill the repos that predate the registry, once.
#
# Observation cannot see the past, so without this every repo already in use
# when the registry landed would stay invisible until it was next opened.
#
# Called from run_all_migrations ahead of the framework rather than written as a
# migration of its own, for the reason adoption is: migrations run in filename
# order, and a project-scoped migration that sorted ahead of the backfill would
# read an empty registry, find nothing, and record itself as applied — the exact
# silent no-op the registry exists to end.
seed_project_registry() {
  _project_ensure_file
  if grep -qF "$_PROJECTS_SEED_MARKER" "$PROJECTS_REGISTRY_FILE"; then
    return 0
  fi
  # Without jq there are no candidates to read, which is indistinguishable from
  # a machine that has none — and recording the marker on that reading would
  # retire the backfill before it ever ran. Sync provisions jq; try again then.
  if ! command -v jq >/dev/null 2>&1; then
    return 0
  fi

  local candidate root seeded=0
  local -a roots=()
  while IFS= read -r candidate; do
    while IFS= read -r root; do
      roots+=("$root")
    done < <(_project_seed_roots "$candidate")
  done < <(_project_seed_candidates)

  for root in "${roots[@]:-}"; do
    if [[ -z "$root" ]] || _project_contains "$root"; then
      continue
    fi
    if project_register "$root"; then
      seeded=$(( seeded + 1 ))
    fi
  done

  printf '%s %s\n' "$_PROJECTS_SEED_MARKER" "$CLAUDE_CONFIG_FILE" >> "$PROJECTS_REGISTRY_FILE"
  if (( seeded > 0 )); then
    info "Project registry backfilled — $seeded repo(s) from $CLAUDE_CONFIG_FILE"
  fi
  return 0
}
