#!/usr/bin/env bash
# generate-machine-profile.sh — builds a machine-level context file for Claude Code.
#
# Produces ~/.claude/machine/machine.md with hardware, OS, runtime versions,
# Docker setup, Git identity, and the project registry. Claude reads this at
# session start to answer environment questions without re-discovering system state.
#
# Usage: generate-machine-profile.sh [--force]
#        --force  Skip the 24h staleness check and regenerate unconditionally.
#
# Exit codes:
#   0 — generated or up-to-date (skipped)
#   1 — unexpected error

set -e

MACHINE_DIR="$HOME/.claude/machine"
PROFILE_FILE="$MACHINE_DIR/machine.md"
STAMP_FILE="$MACHINE_DIR/.last-updated"
STALE_HOURS=24
GIT_ROOTS=("$HOME/git" "$HOME/src" "$HOME/projects" "$HOME/code")

# ── Staleness check ───────────────────────────────────────────────────────────

should_regenerate() {
  [[ "${1:-}" == "--force" ]] && return 0
  [[ ! -f "$STAMP_FILE" ]] && return 0
  local last_updated now elapsed
  last_updated=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
  now=$(date +%s)
  elapsed=$(( (now - last_updated) / 3600 ))
  [[ "$elapsed" -ge "$STALE_HOURS" ]]
}

should_regenerate "$@" || exit 0

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

# Workbench location
workbench_dir=""
for candidate in \
    "$HOME/git/otto-nation/otto-workbench" \
    "$HOME/src/otto-nation/otto-workbench" \
    "$HOME/otto-workbench"; do
  [[ -d "$candidate" ]] && { workbench_dir="$candidate"; break; }
done

# ── Project registry ──────────────────────────────────────────────────────────
# Discover git repos and cross-reference with ~/.claude/projects/ for memory status.

declare -A memory_status=()
for mem_dir in "$HOME/.claude/projects"/*/memory/; do
  [[ -d "$mem_dir" ]] || continue
  local_slug=$(basename "$(dirname "$mem_dir")")
  file_count=$(find "$mem_dir" -maxdepth 1 -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$file_count" -gt 0 ]]; then
    memory_status["$local_slug"]="yes ($file_count files)"
  fi
done

declare -a project_rows=()
for git_root in "${GIT_ROOTS[@]}"; do
  [[ -d "$git_root" ]] || continue
  while IFS= read -r -d '' repo_dir; do
    [[ -d "$repo_dir/.git" ]] || continue
    local_path="${repo_dir/#$HOME/~}"
    name=$(basename "$repo_dir")
    slug="${repo_dir//\//-}"
    mem="${memory_status[$slug]:-no}"
    # Detect primary stack from presence of key files
    stack=""
    [[ -d "$repo_dir/ansible" ]] && stack="ansible"
    [[ -f "$repo_dir/go.mod" ]] && stack="${stack:+$stack,}go"
    [[ -f "$repo_dir/package.json" ]] && stack="${stack:+$stack,}node"
    [[ -f "$repo_dir/pyproject.toml" || -f "$repo_dir/requirements.txt" ]] && \
      stack="${stack:+$stack,}python"
    [[ -f "$repo_dir/build.gradle.kts" || -f "$repo_dir/pom.xml" ]] && \
      stack="${stack:+$stack,}java"
    [[ $(find "$repo_dir" -maxdepth 2 -name '*.sh' 2>/dev/null | wc -l) -gt 3 ]] && \
      [[ -z "$stack" ]] && stack="bash"
    [[ -z "$stack" ]] && stack="—"
    project_rows+=("| $name | $local_path | $stack | $mem |")
  done < <(find "$git_root" -maxdepth 3 -name ".git" -type d -print0 2>/dev/null \
    | sed 's|/.git||g' | tr '\n' '\0' | sort -z)
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
  [[ -n "$workbench_dir" ]] && printf '%s\n' "- otto-workbench: ${workbench_dir/#$HOME/~}"
  printf '\n'

  if [[ ${#project_rows[@]} -gt 0 ]]; then
    printf '## Project Registry\n\n'
    printf '| Project | Path | Stack | Memory |\n'
    printf '|---------|------|-------|--------|\n'
    for row in "${project_rows[@]}"; do
      printf '%s\n' "$row"
    done
    printf '\n'
  fi
} > "$tmp_file"

mv "$tmp_file" "$PROFILE_FILE"
date +%s > "$STAMP_FILE"
