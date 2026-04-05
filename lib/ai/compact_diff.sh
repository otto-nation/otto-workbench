#!/usr/bin/env bash
# Diff compaction helper for AI automation.
# Splits a diff into per-file chunks and greedily includes as many as fit
# within DIFF_MAX_CHARS. Requires core.sh to be sourced first (for DIFF_MAX_CHARS).
#
# This file is kept separate from core.sh because it uses bash arrays —
# core.sh must remain POSIX-compatible since go-task sources it via sh -c.

# _compact_diff FULL_DIFF
# Splits a diff into per-file chunks and greedily includes complete file diffs
# within DIFF_MAX_CHARS (smallest files first, maximising coverage).
# Files that don't fit are listed by name in a trailing note.
_compact_diff() {
  local full_diff="$1"

  # Split diff into per-file chunks on "diff --git" boundaries
  local chunks=()
  local current=""
  while IFS= read -r line; do
    if [[ "$line" == "diff --git "* && -n "$current" ]]; then
      chunks+=("$current")
      current=""
    fi
    current+="${line}"$'\n'
  done <<< "$full_diff"
  [[ -n "$current" ]] && chunks+=("$current")

  local total=${#chunks[@]}
  if [[ $total -eq 0 ]]; then
    printf '%s' "${full_diff:0:$DIFF_MAX_CHARS}"
    return
  fi

  # Build "SIZE INDEX" pairs and sort ascending so smallest files are tried first
  local i size_index_pairs=""
  for (( i=0; i<total; i++ )); do
    size_index_pairs+="${#chunks[$i]} $i"$'\n'
  done

  local budget=$DIFF_MAX_CHARS
  local included_indices=()
  local omitted_names=()
  local size idx fname

  # Use temp files instead of process substitution — sh (used by task) disables < <(...).
  local _sort_tmp _idx_tmp
  _sort_tmp=$(mktemp)
  _idx_tmp=$(mktemp)
  printf '%s' "$size_index_pairs" | sort -n > "$_sort_tmp"
  while IFS=' ' read -r size idx; do
    [[ -z "$size" ]] && continue
    if (( size <= budget )); then
      included_indices+=("$idx")
      (( budget -= size ))
    else
      fname=$(printf '%s' "${chunks[$idx]}" | head -1 | grep -oE ' b/.+$' | sed 's/^ b\///')
      omitted_names+=("${fname:-<file>}")
    fi
  done < "$_sort_tmp"
  rm -f "$_sort_tmp"

  # Reconstruct in original diff order
  local result=""
  printf '%s\n' "${included_indices[@]}" | sort -n > "$_idx_tmp"
  while IFS= read -r idx; do
    [[ -z "$idx" ]] && continue
    result+="${chunks[$idx]}"
  done < "$_idx_tmp"
  rm -f "$_idx_tmp"

  if [[ ${#omitted_names[@]} -gt 0 ]]; then
    local omitted_list
    omitted_list=$(printf '%s\n' "${omitted_names[@]}" | paste -sd ',' -)
    result+="
[${#omitted_names[@]} file(s) omitted — diff too large: $omitted_list]"
  fi

  printf '%s' "$result"
}
