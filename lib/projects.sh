#!/usr/bin/env bash
# The repos on this machine that use otto-workbench.
#
# State file: `$PROJECTS_REGISTRY_FILE` — `projects.registry` under the [state
# root](#rootssh), one absolute path per line with `#` comment lines. Text rather
# than YAML for the reason `migrations.applied` is: every write is an append and
# every read is a scan, and YAML would pay a `yq` fork on each of them. The
# filename is declared once, in [`constants.sh`](#constantssh), and this file
# holds functions only.
#
# A line is a work-tree path, optionally followed by a tab and the repo identity
# every worktree of that repo shares — the realpath of its `--git-common-dir`.
# Only `record_project_repo_ids` writes that second field, from the sync;
# registration stays fork-free, so a line arrives bare and is resolved later.
# Everything that matches a line compares the path ahead of the tab.
#
# Membership means a workbench command actually ran in a repo. Nothing scans for
# candidates — the two consumers that used to, the machine profile generator and
# the checkout-scoped migrations, each carried their own guessed-at list of git
# roots and a depth limit, so a repo cloned anywhere else was invisible and the
# migration recorded itself applied all the same. Registration is an observation,
# so it can only ever be late; `otto-workbench projects add` is what covers a
# repo that joined after something needed to see it.
# `record_project_repo_ids` is the sync-time step that gives each of those lines
# the repo identity behind it, which is how work that belongs to a repo rather
# than to a checkout is done once — see [Execution Flow —
# Migrations](execution-flow.md#migrations). The registrations are:
#
# | Caller | Where the root comes from |
# |--------|---------------------------|
# | Claude's SessionStart hook (`reuse-session-start`) | already resolved for the ceiling scan |
# | `pr` | `ctx.worktree_root` |
# | `otto-workbench ai init` | the repo being scaffolded |
# | `otto-workbench projects add [DIR]` | by hand, for a repo that uses neither |
#
# `project_register` does no discovery of its own and forks nothing: every caller
# has a resolved work-tree root in hand. A path under `$TMPDIR`, `/tmp`,
# `/var/folders`, or the workbench's own state or cache root is refused — `bats`
# builds throwaway repos there and runs validators and pre-commit hooks inside
# them. The `/private` twins of the temp roots are listed too, because callers
# hand over a path `git rev-parse --show-toplevel` already resolved and those two
# are symlinks into `/private` on macOS. A bare repo's container is refused as
# well, holding worktrees rather than being one. `PROJECTS_EXCLUDED_PREFIXES` is
# assignable so a test can register the repos it builds, which are all temporary.
#
# Reads drop entries whose directory is gone, so a work tree removed between
# syncs is invisible to every consumer the moment it goes. The sync makes that
# drop permanent — `run_all_migrations` calls `project_prune` before it walks the
# file — so the registry tracks the repos the machine has rather than every one
# it has ever had. Read-time filtering alone keeps a dead line from being seen,
# not from being stored: on a machine that cuts and removes worktrees as a matter
# of routine the file grows without bound, and every read pays a stat per dead
# line forever. `otto-workbench projects prune` is the same drop on demand.
#
# Nothing is held back for a work tree that is merely unreachable — one on an
# unmounted volume — because no reader distinguishes the two either: such a path
# is already absent from `otto-workbench projects list`, from the machine
# profile, and from every migration sweep, so keeping its line buys nothing it
# does not already have. Losing it costs the append that the next workbench
# command run in that repo makes anyway.
#
# Repeats are dropped on read for a related reason: registration is an
# append guarded by a membership check rather than a lock, so two workbench
# commands starting in one repo at the same moment can each read "absent" and
# each append. Absorbing that where it is read costs nothing; a lock would tax
# every hook to prevent a duplicate line.
#
# `otto-workbench projects forget DIR` canonicalises `DIR` before matching —
# entries are stored as `git rev-parse --show-toplevel` returned them, and the
# comparison is an exact string, so a relative path, one holding `..`, one
# reaching through a symlink, or one naming a subdirectory of the repo all have
# to arrive in that form or a valid request reads as "not in the registry". A
# directory that is already gone can only be normalised lexically, which is the
# right answer for it: whatever entry it matches was written while it still
# existed.
#
# `seed_project_registry` backfills the repos that predate the registry, once per
# machine, from the `.projects` map in `~/.claude.json` — an observation Claude
# Code wrote, not another guess at where repos live. Each key is a session cwd,
# so `_project_seed_roots` turns one into a work-tree root: `git rev-parse
# --show-toplevel` for a normal checkout, and for a bare-repo container — a
# directory that refuses `--show-toplevel` outright — the worktree checked out on
# the branch the container's HEAD names, the same choice `WORKBENCH_STABLE_DIR`
# makes. A container's feature worktrees are deliberately left out: they come and
# go, each would be a row of its own everywhere the registry is read, and any
# still around registers itself the next time a workbench command runs in it.
#
# A `# backfilled from <path>` line inside the file records that the backfill
# ran: the Python half creates the file the first time `pr` registers anything,
# so a backfill keyed on the file's existence would be skipped forever on a
# machine that used a tool before it next synced. Without `jq` it writes no
# marker and returns — no candidates for want of a reader is indistinguishable
# from a machine that has none, and recording the marker on that reading would
# retire the backfill before it ever ran. It is called from `run_all_migrations`
# ahead of the framework rather than written as a migration, for the reason
# adoption is — see [Execution Flow —
# Migrations](execution-flow.md#migrations).
#
# [`ai/lib/workbench_projects.py`](../ai/lib/workbench_projects.py) is the Python
# half — the SessionStart hook and `pr` register through it, against the same
# file in the same shape. It raises nothing: registration is a side effect of a
# command run for some other reason, and a hook that died on an unwritable state
# file would cost a session for a bookkeeping entry. The filename is declared
# once in [`constants.sh`](#constantssh) as `PROJECTS_REGISTRY_NAME` and once in
# `workbench_paths.py`; `tests/workbench_roots.bats` fails when the two drift,
# and `tests/projects.bats` cross-validates the halves against one file.

# Guard: constants must be loaded (provides PROJECTS_REGISTRY_FILE, plus the
# state and cache roots the exclusion rules below refer to)
if [[ -z "${PROJECTS_REGISTRY_FILE:-}" ]]; then
  echo "ERROR: lib/projects.sh requires PROJECTS_REGISTRY_FILE (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

# Resolved from this file's own location rather than $LIB_SRC_DIR: projects.sh
# is always loaded by the ui.sh facade, and a caller that overrides
# WORKBENCH_DIR to sandbox where components live — bin/local/validate-components
# and bin/local/validate-migrations both do — would otherwise point $LIB_SRC_DIR
# at a directory that never held git_layout.sh in the first place. lib/setup.sh
# uses the same fix for the same reason.
_projects_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=git_layout.sh
. "$_projects_lib_dir/git_layout.sh"
unset _projects_lib_dir

# The line the backfill leaves behind so it runs exactly once per machine.
#
# A marker inside the file rather than the file's own existence: the Python half
# creates the file the first time a `pr` invocation registers a repo, and a
# backfill keyed on existence would then be skipped forever on a machine that
# used a tool before it next synced.
_PROJECTS_SEED_MARKER="# backfilled from"

# What separates a registered work tree from the repo identity behind it.
#
# A tab, and the same reason `migrations.applied` uses one: a path may hold
# anything but NUL and newline, `#` and spaces included. Every split is on the
# *first* tab, so a path that somehow carries one still leaves the field ahead
# of it intact.
_PROJECT_FIELD_SEP=$'\t'

# _split_project_line LINE PATH_VAR ID_VAR
# Split a registry line into the work-tree path every entry starts with and the
# repo identity behind it, which is empty for a line written before the identity
# was recorded.
_split_project_line() {
  local -n __path="$2" __id="$3"
  __path="${1%%"$_PROJECT_FIELD_SEP"*}"
  __id=""
  [[ "$1" == "$__path" ]] || __id="${1#*"$_PROJECT_FIELD_SEP"}"
}

# _split_repo_worktree_line LINE ID_VAR PATH_VAR
# Split a `<repo id><TAB><work-tree path>` line — what project_repo_worktrees
# emits, the reverse field order of a registry line — into the repo identity
# and the work-tree path.
_split_repo_worktree_line() {
  local -n __id="$2" __path="$3"
  __id="${1%%"$_PROJECT_FIELD_SEP"*}"
  __path="${1#*"$_PROJECT_FIELD_SEP"}"
}

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
#
# On the path field rather than the whole line: a line that has been given a
# repo identity carries a second field, and a whole-line comparison would read
# it as a different repo — registering the same work tree a second time on
# every sync that resolved one.
_project_contains() {
  [[ -f "$PROJECTS_REGISTRY_FILE" ]] || return 1
  local want="${1%/}" line path id
  while IFS= read -r line || [[ -n "$line" ]]; do
    _split_project_line "$line" path id
    if [[ "$path" == "$want" ]]; then
      return 0
    fi
  done < "$PROJECTS_REGISTRY_FILE"
  return 1
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
#
# A path holding `_PROJECT_FIELD_SEP` is refused alongside the other
# membership rules: the tab is what tells the path field from the repo
# identity, so a path that carries one is indistinguishable from a line that
# already has an identity — `_project_contains` would compare against the
# truncated field 1 forever, never match the real path, and every workbench
# command run in that repo would append another line with no error.
project_register() {
  local dir="${1%/}"
  if [[ "$dir" == *"$_PROJECT_FIELD_SEP"* ]] || _project_excluded "$dir" || ! _project_is_worktree "$dir"; then
    return 1
  fi
  if _project_contains "$dir"; then
    return 3
  fi
  _project_ensure_file || return 2
  printf '%s\n' "$dir" >> "$PROJECTS_REGISTRY_FILE" || return 2
}

# _project_registered_lines — every surviving registry line, whole, one per line.
#
# The filtering both public reads want: comment lines and blanks dropped, a
# repeat of a path dropped, a path whose directory is gone skipped. Callers that
# need the repo identity read the line; project_registered prints the path out
# of it.
_project_registered_lines() {
  [[ -f "$PROJECTS_REGISTRY_FILE" ]] || return 0
  local line path id
  local -A seen=()
  # `|| [[ -n "$line" ]]`: read reports EOF for a final line with no newline
  # after it, and the loop body would never see it.
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" || "$line" == \#* ]]; then
      continue
    fi
    _split_project_line "$line" path id
    if [[ -n "${seen[$path]:-}" ]]; then
      continue
    fi
    seen[$path]=1
    if [[ -d "$path" ]]; then
      printf '%s\n' "$line"
    fi
  done < "$PROJECTS_REGISTRY_FILE"
  return 0
}

# project_registered — print every registered repo that still exists, one per line.
#
# A directory that is gone is skipped rather than rewritten away: a read is the
# wrong place to take a write lock, so correctness never waits on the file being
# tidy. `project_prune` is what makes the drop permanent, from the sync and from
# `otto-workbench projects prune`.
#
# Repeats are dropped here too. Registration is an append guarded by a
# membership check rather than a lock, so two workbench commands starting in the
# same repo at the same moment can each read "absent" and each append. Absorbing
# that on read is what a lock would buy, without making every hook pay for one.
project_registered() {
  local line path id
  while IFS= read -r line; do
    _split_project_line "$line" path id
    printf '%s\n' "$path"
  done < <(_project_registered_lines)
  return 0
}

# _project_rewrite LINES... — replace the registry with exactly these lines.
#
# Built in a temp file and swapped in with `mv` so a process killed mid-write
# never leaves the registry truncated — `project_forget` and `project_prune`
# are explicit, user-invoked commands, and losing entries to a partial write
# there is a lot more surprising than during passive registration.
#
# The swap itself is atomic; the read that produced these lines was not part of
# it. Every caller scans the file, decides what to keep, and calls this — so a
# registration appended in between is overwritten. Stated here rather than at
# each of the three call sites because the window belongs to the shape they
# share, and the sync now opens it twice a run rather than only when an operator
# types `otto-workbench projects prune`.
#
# ceiling: no lock, so that appended registration is lost and the repo
# re-registers the next time a workbench command runs in it — a scan of one file
# against a lock every hook would have to take. Upgrade to a lock if anything
# starts registering repos that do not run commands of their own, since nothing
# would then bring the lost line back.
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
  local line path id
  while IFS= read -r line || [[ -n "$line" ]]; do
    _split_project_line "$line" path id
    if [[ -n "$line" && "$path" != "$dir" ]]; then
      kept+=("$line")
    fi
  done < "$PROJECTS_REGISTRY_FILE"
  _project_rewrite "${kept[@]}"
}

# project_prune — drop entries whose directory is gone, and repeats. Prints how many went.
#
# The sync calls this as well as `otto-workbench projects prune`, so the drop
# every read already makes is not left waiting on someone to ask for it by hand.
# Rewrites only when something went, so a settled machine pays a scan and no
# write.
project_prune() {
  if [[ ! -f "$PROJECTS_REGISTRY_FILE" ]]; then
    echo 0
    return 0
  fi
  local line path id dropped=0
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
    _split_project_line "$line" path id
    # A repeat is what project_registered was already absorbing on read; this is
    # where the absorbing stops being needed. The first line wins, which is the
    # one holding a repo identity when only one of them does — record_project_
    # repo_ids resolves in file order, so an id is never behind a bare repeat.
    if [[ -d "$path" && -z "${seen[$path]:-}" ]]; then
      seen[$path]=1
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

# ─── Repo identity ───────────────────────────────────────────────────────────

# project_repo_id DIR — the identity DIR's repository keeps across its worktrees.
#
# The shared git dir when git can name one, and DIR itself when it cannot. A
# registered directory that is no longer a repository still has to be visited
# exactly once by anything working per repo, and standing for itself is what
# gets it that — the same treatment per-checkout work would have given it.
#
# The fallback is deliberately not recorded: record_project_repo_ids asks git
# directly, so an answer git could not give this sync is asked for again on the
# next one rather than frozen into the registry.
project_repo_id() {
  local dir="$1" shared
  shared="$(git_shared_dir "$dir")" || shared=""
  printf '%s\n' "${shared:-$dir}"
}

# project_repo_label REPO_ID — the directory REPO_ID's repository lives at.
#
# The id is a shared git dir, so the repository is its parent whenever that dir
# is named `.git` — a worktree container and an ordinary clone alike. Anything
# else is printed back unchanged: a bare clone kept as `<name>.git`, and the
# work tree standing for itself that `project_repo_id` falls back to.
project_repo_label() {
  local id="${1%/}"
  if [[ "$id" == */.git && "$id" != "/.git" ]]; then
    printf '%s\n' "${id%/.git}"
    return 0
  fi
  printf '%s\n' "$id"
}

# project_repo_relative REPO_DIR WORK_TREE — WORK_TREE named by the part of its
# path REPO_DIR has not already said, for a display that lists a repo's work
# trees under the repo's own row.
#
# A worktree git was pointed at somewhere else entirely has nothing of its path
# implied by the repo's, so the fallback is the full path, tilde-shortened like
# every other path this display prints.
project_repo_relative() {
  local repo_dir="$1" work_tree="$2" under
  under="${work_tree#"$repo_dir"/}"
  if [[ "$under" == "$work_tree" ]]; then
    under="${work_tree/#$HOME/\~}"
  fi
  printf '%s\n' "$under"
}

# project_repo_worktrees — every registered work tree, under the repo it belongs
# to. Prints `<repo id><TAB><work-tree path>` — the reverse of a registry line,
# which is `<path><TAB><id>` — with a repo's work trees consecutive, repos in
# the order the registry first names one of theirs and each repo's work trees in
# registry order. Both production callers pipe this through `sort`, which
# reorders the repos alphabetically by id but leaves each repo's work trees
# consecutive underneath it.
#
# Grouping is the reading both the list and the machine profile want: a machine
# that cuts a worktree per branch has one repository behind a dozen entries, and
# a flat list of them says nothing about how many repos it actually holds. The
# id comes from the line when the registry holds one, so a whole sweep costs no
# forks on a machine the sync has already resolved.
project_repo_worktrees() {
  local line path id
  local -a order=()
  local -A grouped=()
  while IFS= read -r line; do
    _split_project_line "$line" path id
    if [[ -z "$id" ]]; then
      id="$(project_repo_id "$path")"
    fi
    if [[ -z "${grouped[$id]:-}" ]]; then
      order+=("$id")
      grouped[$id]="$path"
      continue
    fi
    grouped[$id]+=$'\n'"$path"
  done < <(_project_registered_lines)

  for id in "${order[@]}"; do
    while IFS= read -r path; do
      printf '%s%s%s\n' "$id" "$_PROJECT_FIELD_SEP" "$path"
    done <<< "${grouped[$id]}"
  done
  return 0
}

# project_repo_leaders — one registered work tree per repo, with its repo id.
# Prints the same `<repo id><TAB><work-tree path>` line `project_repo_worktrees`
# does, keeping the first of each repo's — so the first surviving work tree of
# each repo wins. The path is what a caller working per repo runs against, and
# the id is what it records the result under.
#
# The leader is not stable and does not need to be: a repo-scoped migration's
# state line names the id, so the leader changing between syncs — because the
# previous one was removed — re-runs nothing. Read off the grouped list rather
# than the registry so one function owns which repo a line belongs to.
project_repo_leaders() {
  local line id path last=""
  while IFS= read -r line; do
    _split_repo_worktree_line "$line" id path
    if [[ "$id" == "$last" ]]; then
      continue
    fi
    last="$id"
    printf '%s\n' "$line"
  done < <(project_repo_worktrees)
  return 0
}

# record_project_repo_ids — give every registry line the repo identity it lacks.
#
# Called from run_all_migrations, after the registry prune and ahead of the
# migration-state pruning that reads the ids back — so the lines it resolves are
# the ones the machine still has.
# Here rather than at registration because both halves of the registry are
# fork-free by design — every caller has a resolved work-tree root in hand, and
# the Python half runs on a session's startup path — so a line arrives bare and
# the sync is what resolves it.
#
# A stored id is trusted while the directory it names is still there. A
# relayout (`git worktree move`, a container renamed, a clone converted to bare)
# moves or removes the shared git dir, and a stat catches that for a fraction of
# what re-resolving every line on every sync would cost.
#
# Like every caller of `_project_rewrite`, this reads the file and swaps a new
# one in unlocked; the marker for what that loses is on the helper.
record_project_repo_ids() {
  [[ -f "$PROJECTS_REGISTRY_FILE" ]] || return 0
  local line path id shared changed=0
  local -a kept=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" ]]; then
      continue
    fi
    if [[ "$line" == \#* ]]; then
      kept+=("$line")
      continue
    fi
    _split_project_line "$line" path id
    if [[ -n "$id" && ! -d "$id" ]]; then
      # Clear it from the line too, not just the local: if re-resolution below
      # fails, the line written back must not still carry the id that just
      # proved stale.
      id=""
      line="$path"
      changed=$(( changed + 1 ))
    fi
    if [[ -z "$id" && -d "$path" ]] && shared="$(git_shared_dir "$path")"; then
      line="$path$_PROJECT_FIELD_SEP$shared"
      changed=$(( changed + 1 ))
    fi
    kept+=("$line")
  done < "$PROJECTS_REGISTRY_FILE"

  if (( changed > 0 )); then
    _project_rewrite "${kept[@]}"
  fi
  return 0
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
  jq -r '(.projects // {}) | keys[]' "$CLAUDE_CONFIG_FILE" 2>/dev/null
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
# order, and a checkout-scoped migration that sorted ahead of the backfill would
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
  local -a roots=() candidates=()
  local seed_output
  if ! seed_output="$(_project_seed_candidates)"; then
    # A present-but-unparseable ~/.claude.json (mid-write, hand-edited syntax
    # error) is not "no candidates" — treat it like the missing-jq case above
    # and skip without recording the marker so the backfill retries later.
    return 0
  fi
  if [[ -n "$seed_output" ]]; then
    mapfile -t candidates <<< "$seed_output"
  fi
  for candidate in "${candidates[@]:-}"; do
    [[ -z "$candidate" ]] && continue
    while IFS= read -r root; do
      roots+=("$root")
    done < <(_project_seed_roots "$candidate")
  done

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
