#!/usr/bin/env bash
# generate-machine-profile.sh — builds a machine-level context file for Claude Code.
#
# Produces ~/.claude/machine/machine.md with hardware, OS, runtime versions,
# Docker setup, Git identity, and the project registry. Claude reads this at
# session start to answer environment questions without re-discovering system state.
#
# Usage: generate-machine-profile.sh [--force] [--diff]
#        --force  Skip the 24h staleness check and regenerate unconditionally.
#        --diff   Back up the current profile, regenerate, and print the diff.
#
# Exit codes:
#   0 — generated or up-to-date (skipped)
#   1 — unexpected error

set -e

_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
# ui.sh rather than constants.sh alone: a location that does not resolve is
# reported with warn, and the facade is what puts the output helpers in scope.
. "$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)/lib/ui.sh"

MACHINE_DIR="$CLAUDE_DIR/machine"
PROFILE_FILE="$MACHINE_DIR/machine.md"
STAMP_FILE="$MACHINE_DIR/.last-updated"
STALE_HOURS=24

# ── Argument parsing ─────────────────────────────────────────────────────────

OPT_FORCE=false
OPT_DIFF=false
for arg in "$@"; do
  case "$arg" in
    --force) OPT_FORCE=true ;;
    --diff)  OPT_DIFF=true ;;
  esac
done

# ── Staleness check ───────────────────────────────────────────────────────────

should_regenerate() {
  [[ "$OPT_FORCE" == true ]] && return 0
  [[ ! -f "$STAMP_FILE" ]] && return 0
  local last_updated now elapsed
  last_updated=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
  now=$(date +%s)
  elapsed=$(( (now - last_updated) / 3600 ))
  [[ "$elapsed" -ge "$STALE_HOURS" ]]
}

should_regenerate || exit 0

# ── Diff setup ────────────────────────────────────────────────────────────────

prev_file=""
if [[ "$OPT_DIFF" == true && -f "$PROFILE_FILE" ]]; then
  prev_file="$(mktemp)"
  cp "$PROFILE_FILE" "$prev_file"
fi

mkdir -p "$MACHINE_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────

# cmd_version CMD ARGS... — runs a version command and extracts the first line.
# Returns "not found" gracefully.
cmd_version() {
  local cmd="$1"; shift
  command -v "$cmd" >/dev/null 2>&1 || { echo "not found"; return; }
  "$cmd" "$@" 2>&1 | head -1
}

# short_version OUTPUT — extracts a semver-like version number from a string.
short_version() {
  echo "$1" | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1
}

# issues_cell SCOPE VALUE OUT — the Issues cell for one resolved record,
# assigned to the variable named by OUT. SCOPE and VALUE are the first two
# fields of an `otto-workbench config get` record.
#
# A repo that declared its own tracker gets the bare value; one that only
# inherits the machine-wide answer is tagged, because the two are different
# facts and a reader acting on the column needs to know which it has. Anything
# else reads as "unset" rather than as a guess or as the "—" the Stack column
# uses for none: an undeclared tracker is an answer still owed, and this table
# is where the set of repos owing one becomes visible. That marker absorbs every
# way the read can come back unusable — a directory deleted since
# project_registered listed it, a config that is missing, unreadable, or
# malformed, and a value that could not occupy a table cell without breaking the
# row. Degrading one row beats failing the profile over a repo the reader was
# not asking about.
issues_cell() {
  local scope="$1" value="$2"
  local -n __cell="$3"
  if [[ ! "$value" =~ ^[A-Za-z0-9._-]+$ ]]; then __cell="unset"; return 0; fi
  if [[ "$scope" == "$WORKBENCH_GLOBAL_SCOPE" ]]; then
    __cell="$value (global)"
    return 0
  fi
  __cell="$value"
}

# ── Collect system facts ──────────────────────────────────────────────────────

os_name=$(sw_vers -productName 2>/dev/null || uname -s)
os_version=$(sw_vers -productVersion 2>/dev/null || uname -r)
arch=$(uname -m)
# Human-readable chip identifier
chip=$(sysctl -n machdep.cpu.brand_string 2>/dev/null \
  || sysctl -n hw.model 2>/dev/null \
  || echo "$arch")
chip=$(echo "$chip" | sed 's/Apple //; s/ Chip//')
ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
ram_gb=$(( ram_bytes / 1024 / 1024 / 1024 ))

# Shell versions
zsh_ver=$(short_version "$(cmd_version zsh --version)")
bash_ver=$(short_version "$(cmd_version bash --version)")

# Runtime versions
go_ver=$(short_version "$(cmd_version go version)")
java_raw=$(cmd_version java -version)
java_ver=$(echo "$java_raw" | grep -oE '"[0-9]+(\.[0-9]+)*"' | tr -d '"' | head -1)
python_ver=$(short_version "$(cmd_version python3 --version)")
node_ver=$(short_version "$(cmd_version node --version)")

# Package manager
brew_count=$(brew list 2>/dev/null | wc -l | tr -d ' ') || brew_count="unknown"

# Docker runtime
docker_runtime="not running"
if [[ -S "$HOME/.colima/default/docker.sock" ]]; then
  docker_runtime="Colima (socket: ~/.colima/default/docker.sock)"
elif [[ -S "/var/run/docker.sock" ]]; then
  docker_runtime="Docker Desktop (socket: /var/run/docker.sock)"
elif command -v docker >/dev/null 2>&1; then
  docker_runtime="installed but socket not found"
fi

# Git identity
git_name=$(git config --global user.name 2>/dev/null || echo "not set")
git_email=$(git config --global user.email 2>/dev/null || echo "not set")
git_signing=$(git config --global gpg.format 2>/dev/null || echo "none")

# Tool managers
has_mise=$(command -v mise >/dev/null 2>&1 && echo "yes" || echo "no")
has_uv=$(command -v uv >/dev/null 2>&1 && echo "yes" || echo "no")
has_task=$(command -v task >/dev/null 2>&1 && echo "yes" || echo "no")

# Workbench location — this script's own repo, not a guess at where it might be.
#
# constants.sh derives WORKBENCH_DIR from its own file location and resolves the
# main worktree for a bare-repo layout, so WORKBENCH_STABLE_DIR is the path that
# stays put across worktree switches. What this replaces was a list of three
# hardcoded candidate paths that matched nothing on a bare-repo machine, leaving
# the location silently absent from the profile — so a miss is now reported in
# the profile and on stderr rather than dropped.
workbench_dir="$WORKBENCH_STABLE_DIR"
if [[ ! -d "$workbench_dir" ]]; then
  warn "otto-workbench location did not resolve: $workbench_dir is not a directory"
  workbench_dir=""
fi

# ── Project registry ──────────────────────────────────────────────────────────
# The repos that use the workbench, from lib/projects.sh, cross-referenced with
# ~/.claude/projects/ for memory status.
#
# The list used to be a `find` over four guessed-at git roots, which missed any
# repo cloned elsewhere and any repo nested past its depth limit. Membership is
# now recorded when a workbench command runs in a repo, so this reads one list
# instead of re-deriving its own.

declare -A memory_status=()
for mem_dir in "$CLAUDE_DIR/projects"/*/memory/; do
  [[ -d "$mem_dir" ]] || continue
  local_slug=$(basename "$(dirname "$mem_dir")")
  file_count=$(find "$mem_dir" -maxdepth 1 -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$file_count" -gt 0 ]]; then
    memory_status["$local_slug"]="yes ($file_count files)"
  fi
done

# One entry per work tree, and a machine that cuts one per branch has a dozen of
# them behind a single repository — so the table is built per repo, with the
# repo's own facts on its row and its checkouts under it. `repo_leader` is the
# work tree those repo facts are read from, since a bare-repo container holds no
# source files of its own and would answer nothing for the Stack column.
declare -a repo_order=()
declare -A repo_leader=() repo_trees=()
while IFS=$'\t' read -r repo_id work_tree; do
  if [[ -z "${repo_leader[$repo_id]:-}" ]]; then
    repo_order+=("$repo_id")
    repo_leader["$repo_id"]="$work_tree"
    repo_trees["$repo_id"]="$work_tree"
    continue
  fi
  repo_trees["$repo_id"]+=$'\n'"$work_tree"
done < <(project_repo_worktrees | sort)

declare -a registered=()
for repo_id in "${repo_order[@]}"; do
  registered+=("${repo_leader[$repo_id]}")
done

# Where each repo files its issues, resolved for every repo in one pass — one
# work tree of each, since the answer is the repo's and the scopes the loader
# reads are the repo's too — and read back keyed by the directory the resolver
# echoed. Reading at render time rather than recording a copy in the registry is
# what makes the column unable to disagree with the repo: the registry is
# machine-local and built from observed use, and a repo fact kept there would go
# stale the moment the repo edited its config.
#
# The resolver is the typed loader, reached through lib/config_cli.py, because
# it is the only reader that knows all three scopes. A bash reader of the
# project file alone is what used to print "unset" for a repo whose tracker is
# recorded above its worktrees — every checkout of it, on a machine whose
# SessionStart line named the tracker correctly in the same session.
declare -A repo_issues=()
# Written through namerefs below, which is not an assignment shellcheck can see.
declare scope value dir cell
if [[ ${#registered[@]} -gt 0 ]]; then
  while IFS= read -r record; do
    wb_config_split_record "$record" scope value dir
    issues_cell "$scope" "$value" cell
    repo_issues["$dir"]="$cell"
  done < <(python3 "$WORKBENCH_DIR/lib/config_cli.py" get \
    "$ISSUE_PROVIDER_CONFIG_KEY" "${registered[@]}")
fi

# memory_cell DIR — whether Claude holds memories for DIR.
#
# Asked of the repo's own directory as well as of each checkout, because a
# session started in a bare-repo container's worktree keeps its memories under
# the container — so a table that only asked the checkouts reported "no" for
# every one of them while the memories sat one level up.
memory_cell() {
  local slug="${1//\//-}"
  printf '%s\n' "${memory_status[$slug]:-no}"
}

# stack_cell DIR — the primary stack of the checkout at DIR, from the files it
# holds, and "—" when none of them says.
stack_cell() {
  local dir="$1" stack=""
  [[ -d "$dir/ansible" ]] && stack="ansible"
  [[ -f "$dir/go.mod" ]] && stack="${stack:+$stack,}go"
  [[ -f "$dir/package.json" ]] && stack="${stack:+$stack,}node"
  [[ -f "$dir/pyproject.toml" || -f "$dir/requirements.txt" ]] && \
    stack="${stack:+$stack,}python"
  [[ -f "$dir/build.gradle.kts" || -f "$dir/pom.xml" ]] && \
    stack="${stack:+$stack,}java"
  [[ $(find "$dir" -maxdepth 2 -name '*.sh' 2>/dev/null | wc -l) -gt 3 ]] && \
    [[ -z "$stack" ]] && stack="bash"
  printf '%s\n' "${stack:-—}"
}

# Every repo gets a row carrying what belongs to the repo. A repo whose only
# work tree is the repo itself — an ordinary clone — is that one row, the way
# every row used to be; one with worktrees is followed by a `↳` row per checkout
# carrying what belongs to the checkout, so a dozen entries read as the one
# repository they are.
declare -a project_rows=()
for repo_id in "${repo_order[@]}"; do
  repo_dir="$(project_repo_label "$repo_id")"
  leader="${repo_leader[$repo_id]}"
  stack="$(stack_cell "$leader")"
  # A repo missing from the map got no record back at all, which the same
  # "unset" covers — see issues_cell for what that marker stands for.
  issues="${repo_issues[$leader]:-unset}"
  project_rows+=("| $(basename "$repo_dir") | ${repo_dir/#$HOME/\~} | $stack | $issues | $(memory_cell "$repo_dir") |")
  if [[ "${repo_trees[$repo_id]}" == "$repo_dir" ]]; then
    continue
  fi
  while IFS= read -r work_tree; do
    under="${work_tree#"$repo_dir"/}"
    # A worktree git was pointed at somewhere else entirely — nothing of its
    # path is implied by the repo's, so the checkout is named by all of it.
    [[ "$under" == "$work_tree" ]] && under="${work_tree/#$HOME/\~}"
    project_rows+=("| ↳ $under | ${work_tree/#$HOME/\~} | | | $(memory_cell "$work_tree") |")
  done <<< "${repo_trees[$repo_id]}"
done

# ── Write profile ─────────────────────────────────────────────────────────────

tmp_file="$(mktemp)"
today=$(date +%Y-%m-%d)

{
  printf '<!-- last-updated: %s | generated by otto-workbench -->\n' "$today"
  printf '# Machine Profile\n\n'

  printf '%s\n' "## Hardware"
  printf '%s\n' "- ${chip} ${ram_gb}GB RAM"
  printf '%s\n\n' "- ${os_name} ${os_version}"

  printf '%s\n' "## Shell & Runtimes"
  printf '%s\n' "- zsh ${zsh_ver}, bash ${bash_ver}"
  [[ "$go_ver" != "not found" ]] && printf '%s\n' "- Go ${go_ver}"
  [[ -n "$java_ver" ]] && printf '%s\n' "- Java ${java_ver}"
  [[ "$python_ver" != "not found" ]] && printf '%s\n' "- Python ${python_ver}"
  [[ "$node_ver" != "not found" ]] && printf '%s\n' "- Node.js ${node_ver}"
  [[ "$has_mise" == "yes" ]] && printf '%s\n' "- mise (runtime version manager)"
  [[ "$has_uv" == "yes" ]] && printf '%s\n' "- uv (Python venv manager)"
  printf '\n'

  printf '%s\n' "## Docker"
  printf '%s\n\n' "- Runtime: ${docker_runtime}"

  printf '%s\n' "## Git Identity"
  printf '%s\n' "- ${git_name} <${git_email}>"
  printf '%s\n\n' "- Signing: ${git_signing}"

  printf '%s\n' "## Key Tools"
  printf '%s\n' "- Homebrew (${brew_count} packages)"
  [[ "$has_task" == "yes" ]] && printf '%s\n' "- task (task runner)"
  if [[ -n "$workbench_dir" ]]; then
    printf '%s\n' "- otto-workbench: ${workbench_dir/#$HOME/\~}"
  else
    printf '%s\n' "- otto-workbench: location unresolved" \
      "  (expected ${WORKBENCH_STABLE_DIR/#$HOME/\~} — re-run \`otto-workbench sync\`)"
  fi
  printf '\n'

  printf '## Project Registry\n\n'
  if [[ ${#project_rows[@]} -gt 0 ]]; then
    printf '| Project | Path | Stack | Issues | Memory |\n'
    printf '|---------|------|-------|--------|--------|\n'
    for row in "${project_rows[@]}"; do
      printf '%s\n' "$row"
    done
    printf '\n%s\n' "_A \`↳\` row is a work tree of the repo above it — Stack and Issues are the repo's, Memory is each directory's own._"
  else
    empty_registry_msg="_No repos registered yet. A repo joins the registry the first"
    empty_registry_msg+=" time a workbench command runs in it — see \`otto-workbench projects\`._"
    printf '%s\n' "$empty_registry_msg"
  fi
  printf '\n'
} > "$tmp_file"

mv "$tmp_file" "$PROFILE_FILE"
date +%s > "$STAMP_FILE"

# ── Diff output ──────────────────────────────────────────────────────────────

if [[ -n "$prev_file" ]]; then
  diff "$prev_file" "$PROFILE_FILE" 2>/dev/null || true
  rm -f "$prev_file"
elif [[ "$OPT_DIFF" == true ]]; then
  echo "(no previous profile to diff)"
fi
