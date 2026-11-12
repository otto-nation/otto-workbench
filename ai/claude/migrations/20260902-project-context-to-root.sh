#!/usr/bin/env bash
# checkout-scoped: rewrites a file inside each work tree's own .claude/, so the
# state line belongs to the work tree and not to the shared git dir.
#
# Migration: move a project's context file from .claude/CLAUDE.md to the repo
# root as AGENTS.md.
#
# Pi resolves one context file per directory and never visits .claude/, so a
# repo scaffolded with the context file in there gives Pi nothing at all —
# while Claude Code reads it and the gap stays invisible. AGENTS.md at the root
# is read by both harnesses; a CLAUDE.md anywhere in the chain would make
# Claude Code ignore it, so the destination is never CLAUDE.md.
#
# Moved, never copied. Two context files is not two harnesses served, it is one
# repo whose instructions drift apart from the first edit onward.

migration_20260902_project_context_to_root() {
  local work_tree="$1"
  local nested="$work_tree/.claude/CLAUDE.md"
  local root="$work_tree/AGENTS.md"

  # Deferred, not NOOP: a repo registered before it was scaffolded gets its
  # .claude/ later, and NOOP here would retire this against a directory that
  # did not exist yet.
  [[ -d "$work_tree/.claude" ]] || return "$MIGRATION_DEFERRED"

  [[ -f "$nested" ]] || return "$MIGRATION_NOOP"

  local existing
  for existing in "$root" "$work_tree/CLAUDE.md" "$work_tree/AGENTS.MD" \
    "$work_tree/CLAUDE.MD" "$work_tree/AGENTS.override.md"; do
    if [[ -e "$existing" ]]; then
      warn "$work_tree has both $(basename "$existing") and .claude/CLAUDE.md — merge them into AGENTS.md by hand; leaving both in place"
      return "$MIGRATION_NOOP"
    fi
  done

  mv "$nested" "$root"
  success "Moved .claude/CLAUDE.md to AGENTS.md in $work_tree"
}
