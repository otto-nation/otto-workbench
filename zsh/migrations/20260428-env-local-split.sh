#!/usr/bin/env bash
# Migration: move uncommented exports from inside ENV markers to below them.
# The ENV-START/ENV-END section becomes read-only (regenerated each sync).
# User values now live below ENV-END.

migration_20260428_env_local_split() {
  local env_file="$HOME/.env.local"
  [[ -f "$env_file" ]] || return 0
  grep -q '# --- ENV-START ---' "$env_file" || return 0

  # Extract uncommented export lines from inside the marker section
  local exports
  exports=$(sed -n '/# --- ENV-START ---/,/# --- ENV-END ---/p' "$env_file" \
    | grep '^export ' || true)
  [[ -n "$exports" ]] || return 0

  # Remove those lines from inside the markers
  local tmp
  tmp=$(mktemp)
  awk '
    /# --- ENV-START ---/ { inside=1 }
    /# --- ENV-END ---/   { inside=0 }
    inside && /^export /  { next }
    { print }
  ' "$env_file" > "$tmp"

  # Write exports to a temp file to avoid awk newline-in-string issues
  local exports_file
  exports_file=$(mktemp)
  printf '%s\n' "$exports" > "$exports_file"

  # Append the extracted exports after ENV-END
  awk -v exports_file="$exports_file" '
    { print }
    /# --- ENV-END ---/ && !done {
      print ""
      while ((getline line < exports_file) > 0) print line
      done=1
    }
  ' "$tmp" > "$env_file"

  rm -f "$tmp" "$exports_file"
  info "Moved user exports below ENV markers in .env.local"
}
