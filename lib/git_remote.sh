#!/usr/bin/env bash
# The remote, its default branch, and whether a branch exists on it.
#
# One ladder for "which branch is trunk", because four callers had grown their
# own: the AI automation in `lib/ai/`, the global pre-push hook, this repo's own
# pre-push hook, and the surface-compatibility gate. Three of them spelled
# `main` as a literal and the fourth walked `origin/HEAD`, `origin/main`,
# `origin/master` by hand, so a `master` repo got a different answer depending
# on which one asked.
#
# Two contracts, deliberately separate. [`resolve_default_branch`](#resolve_default_branch)
# always answers, because a caller printing a hint needs a name even when it is
# a guess. [`default_base_ref`](#default_base_ref) refuses to answer unless the
# ref is really there, because a caller about to diff against it turns a wrong
# guess into a failure with somebody else's error message on it.
#
# It has no dependencies, so a caller that has not loaded the facade can source
# it on its own — which the global pre-push hook does, since `lib/ai/core.sh`
# would drag the whole AI configuration surface into every push on the machine:
#
# ```bash
# . "$WORKBENCH_DIR/lib/git_remote.sh"
# base="$(default_base_ref)" || base=""
# ```
#
# Every function takes the repository directory as its last optional argument,
# defaulting to `.`. `git -C .` is the cwd, so a caller that has already changed
# directory passes nothing and reads exactly as it did before. A positional and
# not a `-C` flag, so no bash array is needed to pass it on — see below.
#
# POSIX only, for the same reason `conventions.sh` is: `lib/ai/core.sh` sources
# both, and go-task runs the tasks that source it under `/bin/sh`. So no `[[`,
# no `<<<`, no arrays, no pattern-replacement expansion.

[ -n "${_LIB_GIT_REMOTE_SH:-}" ] && return
_LIB_GIT_REMOTE_SH=1

# Git remote name used for push/fetch/range operations.
# shellcheck disable=SC2034  # read by lib/ai/pr.sh and the functions below
GIT_REMOTE="origin"

# resolve_default_branch [DIR]
# Resolves the remote's default branch and prints the name to stdout. DIR is the
# repository to ask, defaulting to the current directory. Always answers.
#
# An unfetched clone, a `wt-init`-converted repo, or any remote whose HEAD was
# never pointed with `git remote set-head origin -a` all lack the symref this
# depends on. When it is missing, a remote-tracking ref that actually exists
# beats a literal guess: "main" then "master" via a local `show-ref` (no
# network call), and only when neither is present does the literal "main" win.
#
# symbolic-ref, not rev-parse --abbrev-ref: when refs/remotes/$GIT_REMOTE/HEAD is
# missing, rev-parse still echoes "$GIT_REMOTE/HEAD" to stdout (then exits 128), so
# the string survives a sed strip as a non-empty "HEAD" and defeats a "${VAR:-main}"
# fallback. symbolic-ref prints nothing on failure, so the fallback here actually fires.
resolve_default_branch() {
  local dir="${1:-.}"
  local branch
  branch=$(git -C "$dir" symbolic-ref "refs/remotes/$GIT_REMOTE/HEAD" 2>/dev/null | sed "s@^refs/remotes/$GIT_REMOTE/@@")
  if [ -n "$branch" ]; then
    printf '%s\n' "$branch"
    return
  fi

  local candidate
  for candidate in main master; do
    remote_branch_ref_exists "$candidate" "$dir" || continue
    printf '%s\n' "$candidate"
    return
  done
  printf 'main\n'
}

# remote_branch_ref_exists BRANCH [DIR]
# True when BRANCH has a remote-tracking ref under $GIT_REMOTE
# (refs/remotes/$GIT_REMOTE/BRANCH) in DIR, which defaults to the current directory.
#
# Companion to resolve_default_branch: that function derives a branch name — guessing when
# the origin/HEAD symref is missing — and this answers whether the result actually exists as
# a ref. Takes the branch as an argument (not just the resolved default) so callers can also
# validate an explicit override, such as a user-supplied --base.
remote_branch_ref_exists() {
  local branch="$1"
  local dir="${2:-.}"
  git -C "$dir" show-ref --verify --quiet "refs/remotes/$GIT_REMOTE/$branch"
}

# default_base_ref [DIR]
# Prints "$GIT_REMOTE/<default branch>" for DIR (default: the current directory)
# when that remote-tracking ref resolves, and returns 1 without printing when it
# does not.
#
# The fallible half of the pair, for callers that are about to hand the answer to
# `git diff` or `git merge-base`. resolve_default_branch ends in a literal "main"
# so that a caller with something to print always has a name; passing that guess
# to git yields "unknown revision", which reads as the repository being broken
# rather than as this ladder having run out of rungs. Returning 1 lets the caller
# say so in its own words, or fall back to a check that needs no base at all.
#
# Composed from the two above rather than walking its own ref list, so "which
# branch is trunk" is still answered in exactly one place.
default_base_ref() {
  local dir="${1:-.}"
  local branch
  branch=$(resolve_default_branch "$dir")
  remote_branch_ref_exists "$branch" "$dir" || return 1
  printf '%s\n' "$GIT_REMOTE/$branch"
}
