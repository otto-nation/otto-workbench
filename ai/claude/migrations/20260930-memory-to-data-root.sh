#!/usr/bin/env bash
set -e
# Migration: carry per-project memory out of Claude's tree into the data root,
# keyed by repo identity, and split the gate stamps out of it.
#
# Machine-scoped, so no work tree is passed: the sweep is over every slug under
# ~/.claude/projects, and the orphans this is meant to report are exactly the
# ones no work tree would reach.
#
# Copied and not moved, and the source is renamed rather than deleted. Memory is
# authored by a dream pass from transcripts that then rotate away, so there is
# no producer to re-run it — that is why it lands under the data root rather
# than beside the regenerable state. A migration that loses it loses it for
# good, so the old tree is left as memory-migrated-<date>/ for a person to
# delete once they have looked.
#
# The stamps go somewhere else on purpose. .last-dream and .last-promote are
# cooldowns, which the gates rewrite on the next pass; they belong with the
# other gate stamps under $GATE_STAMPS_DIR, not among the authored files.
# Newest wins where several worktree slugs carry one: an older stamp would
# reopen a window that has already closed.

migration_20260930_memory_to_data_root() {
  # Sourced here rather than at file scope: the framework sources every
  # migration file to discover its function, so a file-scope source runs on
  # every migration run including the ones that never call this. The helpers
  # below — _encode_slug, _repo_key, _gate_repo_dir, _gate_stamp_file — live
  # in lib/ai/session-count.sh and are on nothing else's load path here, and
  # without them every slug resolves as an unresolvable orphan and no memory
  # is carried at all.
  # shellcheck source=../../../lib/ai/session-count.sh
  . "$LIB_SRC_DIR/ai/session-count.sh"

  local projects_dir="$CLAUDE_DIR/projects"

  # NOOP rather than DEFERRED: a machine with no Claude projects tree has
  # nothing to carry and will not grow one that needs carrying — every writer
  # now writes the new location.
  [[ -d "$projects_dir" ]] || return "$MIGRATION_NOOP"

  local stamp_date carried=0 merged=0 orphaned=0 unresolved=0
  stamp_date="$(date +%Y%m%d)"

  local mem_dir slug repo_dir key dest
  for mem_dir in "$projects_dir"/*/memory; do
    [[ -d "$mem_dir" ]] || continue
    slug="$(basename "$(dirname "$mem_dir")")"

    # Forward from the registry, never by decoding the slug: Claude's transform
    # maps '/', '-' and '.' alike, so a directory name cannot say which repo it
    # came from. A transcript records its own cwd, which is a fact rather than
    # an inference, so that is the fallback.
    repo_dir="$(_migration_repo_for_slug "$slug")" || repo_dir=""

    if [[ -z "$repo_dir" ]]; then
      _migration_report_orphan "$mem_dir"
      orphaned=$((orphaned + 1))
      unresolved=1
      continue
    fi

    key="$(_repo_key "$repo_dir")" || {
      warn "Could not key $repo_dir — left $mem_dir in place"
      unresolved=1
      continue
    }
    dest="$WORKBENCH_MEMORY_DIR/$key"
    mkdir -p "$dest"

    _migration_carry_topics "$mem_dir" "$dest" "$slug" merged

    # Through _gate_repo_dir, not the slug's own path: a stamp is per repo, and
    # several worktree slugs arrive here for one repo. Slugging each of their
    # paths would write one stamp per worktree and leave the cooldown as
    # per-worktree as it was before the move.
    local gate_dir
    gate_dir="$(_gate_repo_dir "$repo_dir")"
    _migration_carry_stamp "$mem_dir/.last-dream" "$(_gate_stamp_file "$gate_dir" 'last-dream')"
    _migration_carry_stamp "$mem_dir/.last-promote" "$(_gate_stamp_file "$gate_dir" 'last-promote')"

    mv "$mem_dir" "$(dirname "$mem_dir")/memory-migrated-$stamp_date"
    carried=$((carried + 1))
  done

  if [[ "$carried" -eq 0 && "$orphaned" -eq 0 ]]; then
    return "$MIGRATION_NOOP"
  fi

  success "Carried $carried memory directory/directories to $WORKBENCH_MEMORY_DIR"
  [[ "$merged" -gt 0 ]] && info "$merged file(s) kept under a slug-qualified name — several worktrees held the same topic"
  [[ "$orphaned" -gt 0 ]] && warn "$orphaned memory directory/directories could not be resolved and were left in place"

  # Non-zero so the next sync retries: a repo that gains a registry entry, or a
  # transcript that is still on disk, resolves on a later run. Reported rather
  # than guessed at.
  [[ "$unresolved" -eq 1 ]] && return 1
  return 0
}

# _migration_repo_for_slug SLUG — the repo path SLUG names, or non-zero.
#
# The registry first, since that is the SSOT for which repos exist; then any
# transcript under the slug, which carries its own cwd.
_migration_repo_for_slug() {
  local slug="$1" line id worktree candidate

  # Every worktree, not just the leader: a slug names one checkout's cwd, and
  # the whole point of this migration is that several of them share one repo.
  #
  # Compared in both the physical and the as-written spelling. On macOS /tmp
  # and /var are symlinks, so a slug encoded from the path a session recorded
  # does not match one encoded from the same path canonicalised, and the repo
  # would read as unresolvable.
  while IFS= read -r line; do
    _split_repo_worktree_line "$line" id worktree
    candidate="$(_migration_match_slug "$slug" "$worktree" "$(project_repo_label "$id")")" || continue
    printf '%s' "$candidate"
    return 0
  done < <(project_repo_worktrees)

  candidate="$(_migration_cwd_from_transcripts "$CLAUDE_DIR/projects/$slug")" || return 1
  [[ -n "$candidate" && -d "$candidate" ]] || return 1
  printf '%s' "$candidate"
}

# _migration_report_orphan MEM_DIR — name an unresolvable directory and its files.
_migration_report_orphan() {
  local mem_dir="$1" f
  warn "Could not resolve a repo for $mem_dir — left in place"
  for f in "$mem_dir"/*; do
    # if/then rather than `[[ ]] &&`: this is the last statement in the loop
    # body, so a final iteration whose test is false makes the loop — and the
    # function — return 1, which under `set -e` fails the whole migration on
    # an empty directory.
    if [[ -e "$f" ]]; then
      info "  holds $(basename "$f")"
    fi
  done
  return 0
}

# _migration_match_slug SLUG CANDIDATE... — the first candidate encoding to SLUG.
#
# Each candidate is tried as written and canonicalised. On macOS /tmp and /var
# are symlinks, so a slug encoded from the path a session recorded does not
# match one encoded from the same path resolved, and the repo would read as
# unresolvable.
_migration_match_slug() {
  local slug="$1" candidate physical
  shift
  for candidate in "$@"; do
    [[ -n "$candidate" ]] || continue
    if [[ "$(_encode_slug "$candidate" 'A-Za-z0-9')" == "$slug" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
    physical="$(cd "$candidate" 2>/dev/null && pwd -P)" || continue
    if [[ "$(_encode_slug "$physical" 'A-Za-z0-9')" == "$slug" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

# _migration_cwd_from_transcripts DIR — the cwd a transcript under DIR records.
_migration_cwd_from_transcripts() {
  local dir="$1" f cwd
  for f in "$dir"/*.jsonl; do
    [[ -f "$f" ]] || continue
    cwd="$(grep -o '"cwd":"[^"]*"' "$f" 2>/dev/null | head -1 | cut -d'"' -f4)" || true
    if [[ -n "$cwd" ]]; then
      printf '%s' "$cwd"
      return 0
    fi
  done
  return 1
}

# _migration_carry_topics SRC DEST SLUG MERGED_VAR — copy topic files across.
#
# Several worktree slugs map to one repo key, so a name collision is expected
# rather than exceptional. A colliding file is kept as <name>.<slug>.md and
# reported: merging two repos' notes silently is the one outcome that cannot be
# undone by hand afterwards.
_migration_carry_topics() {
  local src="$1" dest="$2" slug="$3"
  local -n __merged="$4"
  local f base

  for f in "$src"/*.md; do
    [[ -f "$f" ]] || continue
    base="$(basename "$f")"
    if [[ -e "$dest/$base" ]] && ! cmp -s "$f" "$dest/$base"; then
      cp "$f" "$dest/${base%.md}.$slug.md"
      info "  kept $base from $slug as ${base%.md}.$slug.md"
      __merged=$((__merged + 1))
      continue
    fi
    cp "$f" "$dest/$base"
  done
}

# _migration_carry_stamp SRC DEST — carry one gate stamp, newest wins.
_migration_carry_stamp() {
  local src="$1" dest="$2" old new
  [[ -f "$src" ]] || return 0
  mkdir -p "$(dirname "$dest")"
  if [[ -f "$dest" ]]; then
    old="$(cat "$dest" 2>/dev/null || echo 0)"
    new="$(cat "$src" 2>/dev/null || echo 0)"
    [[ "${new:-0}" -gt "${old:-0}" ]] || return 0
  fi
  cp "$src" "$dest"
}
