#!/usr/bin/env bash
# generate-anatomy.sh — builds a compact file index for Claude Code.
#
# Produces .claude/anatomy.md with per-file line counts, token estimates,
# and descriptions extracted from source comments. Claude reads this on-demand
# to decide which files to open, reducing unnecessary token usage.
#
# Usage: generate-anatomy.sh [PROJECT_ROOT]
#        Defaults to git repo root of the current directory.
#
# Exit codes:
#   0 — generated, up-to-date, or skipped (not a git repo / no .claude/)
#   1 — unexpected error

set -e

# ── Config ───────────────────────────────────────────────────────────────────

TOKENS_PER_LINE=4
MAX_FILE_LINES=10000
MAX_FILES=2000

# Files to skip even if tracked (locks, generated, vendored)
SKIP_PATTERNS=(
  "package-lock.json" "yarn.lock" "pnpm-lock.yaml" "bun.lockb" "bun.lock"
  "go.sum" "Gemfile.lock" "composer.lock" "Cargo.lock" "poetry.lock"
  "Pipfile.lock" "flake.lock" "packages.lock.json"
  "*.min.js" "*.min.css" "*.bundle.js" "*.chunk.js"
  "*.generated.*" "*.pb.go" "*.pb.h" "*.pb.cc"
)

# Binary extensions to exclude
BINARY_EXTS=(
  png jpg jpeg gif bmp ico svg webp avif
  woff woff2 ttf eot otf
  zip tar gz bz2 xz 7z rar
  pdf doc docx xls xlsx ppt pptx
  exe dll so dylib o a class jar war
  mp3 mp4 wav avi mov mkv flac ogg
  db sqlite sqlite3
  pyc pyo whl
  DS_Store
)

# ── Helpers ──────────────────────────────────────────────────────────────────

# is_binary_ext FILE — returns 0 if the file has a binary extension
is_binary_ext() {
  local ext="${1##*.}"
  ext="${ext,,}"  # lowercase
  local b
  for b in "${BINARY_EXTS[@]}"; do
    [[ "$ext" == "$b" ]] && return 0
  done
  return 1
}

# matches_skip FILE — returns 0 if file matches a skip pattern
matches_skip() {
  local file="$1" base pattern
  base="$(basename "$file")"
  for pattern in "${SKIP_PATTERNS[@]}"; do
    # shellcheck disable=SC2254
    case "$base" in $pattern) return 0 ;; esac
  done
  return 1
}

# extract_description FILE — prints the first meaningful comment from lines 1-10
extract_description() {
  local file="$1" line desc=""
  local count=0 in_frontmatter=false

  while IFS= read -r line || [[ -n "$line" ]]; do
    count=$((count + 1))
    [[ $count -gt 15 ]] && break

    # Skip YAML frontmatter blocks (--- ... ---)
    if [[ $count -eq 1 && "$line" == "---" ]]; then
      in_frontmatter=true
      continue
    fi
    if [[ "$in_frontmatter" == true ]]; then
      [[ "$line" == "---" ]] && in_frontmatter=false
      continue
    fi

    # Skip blank lines, shebangs, package/import statements
    [[ -z "${line// /}" ]] && continue
    [[ "$line" =~ ^#! ]] && continue
    [[ "$line" =~ ^[[:space:]]*(package|import|from|require|use|using|include|module) ]] && continue

    # Single-line comments: // # -- ;;
    if [[ "$line" =~ ^[[:space:]]*(//|#|--|;;)[[:space:]]+(.*) ]]; then
      desc="${BASH_REMATCH[2]}"
      # Skip common noise: shellcheck, eslint, pragma, license headers
      [[ "$desc" =~ ^(shellcheck|eslint|prettier|@|TODO|FIXME|NOTE|Copyright|License|SPDX) ]] && continue
      [[ -n "$desc" ]] && break
    fi

    # Block comment openers: /* /** """ '''
    if [[ "$line" =~ ^[[:space:]]*/\*\*?[[:space:]]*(.*) ]]; then
      desc="${BASH_REMATCH[1]}"
      desc="${desc%\*/}"  # strip inline close
      desc="${desc# }"
      [[ -n "$desc" ]] && break
    fi

    # Python/Ruby docstrings
    if [[ "$line" =~ ^[[:space:]]*(\"\"\"|\'\'\')[[:space:]]*(.*) ]]; then
      desc="${BASH_REMATCH[2]}"
      desc="${desc%\"\"\"}"
      desc="${desc%\'\'\'}"
      [[ -n "$desc" ]] && break
    fi

    # Markdown: use first heading text as description
    if [[ "$line" =~ ^#+(\ |	)+(.*) ]]; then
      desc="${BASH_REMATCH[2]}"
      [[ -n "$desc" ]] && break
    fi

  done < "$file"

  # Truncate to 60 chars
  if [[ ${#desc} -gt 60 ]]; then
    desc="${desc:0:57}..."
  fi

  printf '%s' "$desc"
}

# label_from_filename FILE — derives a human-readable label from the filename
label_from_filename() {
  local base="${1##*/}"
  base="${base%.*}"                 # strip extension
  base="${base//_/ }"               # underscores to spaces
  base="${base//-/ }"               # hyphens to spaces
  # Capitalize first letter
  printf '%s' "${base^}"
}

# ── Ansible section ──────────────────────────────────────────────────────────

# generate_ansible_section OUTPUT_FILE — appends a "Service Stack" section to the
# output file if an Ansible inventory with versions.yml is found. Pure bash; no yq/jq.
generate_ansible_section() {
  local out="$1"

  # Find versions.yml — walk group_vars subdirs
  local versions_file="" services_file=""
  local gvars_dir
  for gvars_dir in ansible/inventory/group_vars/*/; do
    [[ -f "${gvars_dir}versions.yml" ]] || continue
    versions_file="${gvars_dir}versions.yml"
    [[ -f "${gvars_dir}services.yml" ]] && services_file="${gvars_dir}services.yml"
    break
  done

  [[ -z "$versions_file" ]] && return 0

  # Parse versions: key pattern is <service>_version: "value"
  declare -A svc_versions=()
  local line key val
  while IFS= read -r line; do
    if [[ "$line" =~ ^([a-z0-9_]+)_version:[[:space:]]+\"?([^\"[:space:]#]+) ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      val="${val//\"/}"
      svc_versions["$key"]="$val"
    fi
  done < "$versions_file"

  [[ ${#svc_versions[@]} -eq 0 ]] && return 0

  # Parse ports and container names from services.yml
  declare -A svc_ports=()
  declare -A svc_containers=()
  if [[ -n "$services_file" ]]; then
    while IFS= read -r line; do
      [[ "$line" =~ ^([a-z0-9_]+)_port:[[:space:]]+([0-9]+) ]] && { svc_ports["${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"; continue; }
      [[ "$line" =~ ^([a-z0-9_]+)_container:[[:space:]]+([a-z0-9_-]+) ]] && svc_containers["${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"
    done < "$services_file"
  fi

  # Emit section
  {
    printf '## Service Stack\n\n'
    printf '<!-- Auto-generated from %s -->\n\n' "$versions_file"
    printf '| Service | Container | Version | Port |\n'
    printf '|---------|-----------|---------|------|\n'

    local svc
    while IFS= read -r svc; do
      # Skip sub-service version keys (immich_postgres, immich_ml, etc.)
      case "$svc" in *_postgres|*_ml|*_redis) continue ;; esac

      local version="${svc_versions[$svc]}"
      local container="${svc_containers[$svc]:-—}"
      # Fallback: try <svc>_server container (e.g., immich_server_container)
      [[ "$container" == "—" && -n "${svc_containers[${svc}_server]:-}" ]] && \
        container="${svc_containers[${svc}_server]}"
      # Port: try <svc>_port, then <svc>_admin_port (e.g., adguard_admin_port)
      local port="${svc_ports[$svc]:-${svc_ports[${svc}_admin]:-—}}"
      # Special case: caddy has separate http/https ports
      if [[ "$svc" == "caddy" ]]; then
        local http="${svc_ports[caddy_http]:-}" https="${svc_ports[caddy_https]:-}"
        [[ -n "$http" && -n "$https" ]] && port="${http}/${https}"
      fi

      printf '| %s | %s | %s | %s |\n' "$svc" "$container" "$version" "$port"
    done < <(printf '%s\n' "${!svc_versions[@]}" | sort)

    printf '\n'
  } >> "$out"
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
  local project_root="${1:-}"

  # Resolve git repo root
  if [[ -z "$project_root" ]]; then
    project_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
  fi

  cd "$project_root" || exit 0

  # Require .claude/ directory (project must be initialized)
  [[ -d ".claude" ]] || exit 0

  # ── Staleness check ──────────────────────────────────────────────────────
  local current_hash anatomy_file=".claude/anatomy.md"
  current_hash="$(git rev-parse HEAD 2>/dev/null)" || exit 0

  if [[ -f "$anatomy_file" ]]; then
    local stored_hash
    stored_hash="$(head -2 "$anatomy_file" | sed -n 's/.*git: \([a-f0-9]*\).*/\1/p' 2>/dev/null || true)"
    if [[ "$stored_hash" == "$current_hash" ]]; then
      exit 0
    fi
  fi

  # ── Collect files ────────────────────────────────────────────────────────
  local -a files=()
  local file
  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    is_binary_ext "$file" && continue
    matches_skip "$file" && continue
    [[ -f "$file" ]] || continue
    files+=("$file")
  done < <(git ls-files 2>/dev/null)

  local file_count=${#files[@]}
  if [[ $file_count -eq 0 ]]; then
    exit 0
  fi

  # ── Large repo fallback ────────────────────────────────────────────────
  # TODO: directory-level summary for repos over MAX_FILES
  # For now, truncate to MAX_FILES
  if [[ $file_count -gt $MAX_FILES ]]; then
    files=("${files[@]:0:$MAX_FILES}")
    file_count=$MAX_FILES
  fi

  # ── Count lines (batched) ─────────────────────────────────────────────
  local -A line_counts=()
  local wc_line
  while IFS= read -r wc_line; do
    # wc -l output: "   123 path/to/file"
    local count path
    count="$(echo "$wc_line" | awk '{print $1}')"
    path="$(echo "$wc_line" | awk '{$1=""; print substr($0,2)}')"
    [[ -n "$path" ]] && line_counts["$path"]="$count"
  done < <(printf '%s\n' "${files[@]}" | xargs wc -l 2>/dev/null | grep -v ' total$')

  # ── Build output ──────────────────────────────────────────────────────
  local tmp_file total_tokens=0
  tmp_file="$(mktemp)"

  # Group files by directory
  local -A dir_files=()
  for file in "${files[@]}"; do
    local dir
    dir="$(dirname "$file")"
    [[ "$dir" == "." ]] && dir="(root)"
    dir_files["$dir"]+="${file}"$'\n'
  done

  # Sort directories
  local -a sorted_dirs=()
  while IFS= read -r dir; do
    sorted_dirs+=("$dir")
  done < <(printf '%s\n' "${!dir_files[@]}" | sort)

  # Write header (placeholder — we'll update total tokens after)
  {
    printf '# Project Anatomy\n'
    printf '<!-- Generated by project-anatomy | git: %s | files: %d | est. tokens: ~PLACEHOLDER -->\n' \
      "$current_hash" "$file_count"
    printf '<!-- Read this file to understand the project layout before exploring. -->\n\n'
  } > "$tmp_file"

  # Write Ansible service stack section (no-op if not an Ansible repo)
  generate_ansible_section "$tmp_file"

  # Write directory sections
  for dir in "${sorted_dirs[@]}"; do
    local dir_display="$dir"
    [[ "$dir" == "(root)" ]] && dir_display="."

    {
      printf '## %s/\n\n' "$dir_display"
      printf '| File | Lines | ~Tokens | Description |\n'
      printf '|------|------:|--------:|-------------|\n'

      local entry
      while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue

        local lines="${line_counts[$entry]:-0}"

        # Skip very large files
        [[ $lines -gt $MAX_FILE_LINES ]] && continue

        local tokens=$((lines * TOKENS_PER_LINE))
        total_tokens=$((total_tokens + tokens))

        local display_name
        [[ "$dir" == "(root)" ]] \
          && display_name="$entry" \
          || display_name="${entry#"$dir"/}"

        local desc
        desc="$(extract_description "$entry")"
        [[ -z "$desc" ]] && desc="$(label_from_filename "$entry")"

        printf '| %s | %d | %d | %s |\n' "$display_name" "$lines" "$tokens" "$desc"
      done <<< "${dir_files[$dir]}"

      printf '\n'
    } >> "$tmp_file"
  done

  # Format total tokens as human-readable (e.g., 45200 -> 45k)
  local tokens_display
  if [[ $total_tokens -ge 1000 ]]; then
    tokens_display="$((total_tokens / 1000))k"
  else
    tokens_display="$total_tokens"
  fi

  # Replace placeholder with actual total
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s/~PLACEHOLDER/~${tokens_display}/" "$tmp_file"
  else
    sed -i "s/~PLACEHOLDER/~${tokens_display}/" "$tmp_file"
  fi

  # Atomic write
  mv "$tmp_file" "$anatomy_file"
}

main "$@"
