#!/usr/bin/env bash
# description: Cross-harness agent skills
# Skill setup steps — sourced by ai/setup.sh.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).
#
# This directory holds the canonical skills and nothing else that a `*/` glob can
# reach — every consumer enumerates skills that way, so a subdirectory added here
# is read as a skill. Migrations for this subsystem live under the harness whose
# path they drain.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

AGENT_PROTOCOL_PLACEHOLDER_MARKER="<!-- AGENT_PROTOCOL_PLACEHOLDER:"

# Dropped into every skill directory this step materialises, and the only thing
# that tells a stale workbench install apart from a hand-written skill when the
# pruning loop meets a real directory. Both discovery roots are documented homes
# for personal skills, so "absent from the source tree" is not on its own a
# licence to delete.
SKILL_INSTALL_MARKER=".installed-by-otto-workbench"

# _resolve_agent_file AGENT — prints the path to an agent's markdown, honouring
# the user override layer.
_resolve_agent_file() {
  local agent="$1"
  if [[ -f "$USER_AGENTS_DIR/${agent}.md" ]]; then
    printf '%s' "$USER_AGENTS_DIR/${agent}.md"
    return 0
  fi
  printf '%s' "$CLAUDE_AGENTS_SRC_DIR/${agent}.md"
}

# _clear_skill_entry PATH — empties a discovery-root slot the workbench owns,
# returning 1 with the slot untouched when it holds something the user wrote.
#
# A symlink is always the workbench's: install_symlink is the only thing that
# writes one into a discovery root, so removing it is unconditional. A real
# directory is the workbench's only when it carries SKILL_INSTALL_MARKER, which
# _install_agent_skill drops in every directory it materialises — both roots are
# documented homes for hand-written skills, and silently deleting one of those
# is not this step's call to make. An empty slot is already in the shape the
# caller wants, so it succeeds.
_clear_skill_entry() {
  local path="$1"
  if [[ -L "$path" ]]; then
    rm -f "$path"
    return 0
  fi
  [[ -d "$path" ]] || return 0
  if [[ ! -f "$path/$SKILL_INSTALL_MARKER" ]]; then
    warn "$path was not installed by the workbench — leaving it in place"
    return 1
  fi
  rm -rf "${path:?}"
}

# _install_agent_skill SOURCE_DIR TARGET_DIR AGENT — writes a real SKILL.md with
# the agent's protocol body spliced in place of the placeholder comment.
#
# A real file rather than a symlink because the content does not exist on disk
# anywhere: it is the skill's frontmatter joined to a body that lives in the
# agent file. Rewritten wholesale on every run so a re-sync cannot append.
#
# The body is read before anything is removed, so a protocol that cannot be
# spliced leaves the previous good install alone instead of replacing it with a
# skill whose instructions are missing.
_install_agent_skill() {
  local source_dir="$1" target_dir="$2" agent="$3"
  local name agent_file body
  name="$(basename "$source_dir")"
  agent_file="$(_resolve_agent_file "$agent")"

  if [[ ! -f "$agent_file" ]]; then
    warn "Agent file missing for skill $name: $agent_file — skipping"
    return 0
  fi

  body="$(awk 'BEGIN { n = 0 }
               /^---$/ { n++; if (n == 2) { found = 1; next } }
               found { print }' "$agent_file")"
  if [[ -z "$body" ]]; then
    warn "Agent file has no body after its frontmatter: $agent_file — skipping $name"
    return 0
  fi

  _clear_skill_entry "$target_dir" || return 0
  mkdir -p "$target_dir"

  # The ownership marker lands before the content, not after. An install
  # interrupted between the two then leaves a marked, incomplete directory that
  # the next run clears and rewrites; written last, the same interruption would
  # leave a real directory with no marker, which _clear_skill_entry classifies
  # as hand-written forever — warning on every sync and never repairing itself.
  : > "$target_dir/$SKILL_INSTALL_MARKER"
  awk -v marker="$AGENT_PROTOCOL_PLACEHOLDER_MARKER" \
    'index($0, marker) { exit } { print }' \
    "$source_dir/SKILL.md" > "$target_dir/SKILL.md"
  printf '%s\n' "$body" >> "$target_dir/SKILL.md"
}

# _prune_skills TARGET_DIR LAYERS_VAR — removes the entries in TARGET_DIR that
# the workbench owns and that neither the default nor the override layer provides.
#
# The __ prefix on the nameref is required: a local sharing the caller's variable
# name would shadow the nameref's target and the assignment would silently land
# in this scope instead.
_prune_skills() {
  local target="$1"
  local -n __skill_layers=$2
  local item entry name
  for item in "$target"/*/; do
    entry="${item%/}"
    [[ -L "$entry" || -d "$item" ]] || continue
    name=$(basename "$item")
    [[ -z "${__skill_layers[$name]+set}" ]] || continue

    _clear_skill_entry "$entry" || continue
    [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}⊘ pruned $name${NC}" || true
  done
  return 0
}

# step_skills — installs every workbench skill into both harnesses' discovery roots.
#
# Claude Code reads ~/.claude/skills/ and Pi reads ~/.agents/skills/; both follow
# symlinks, so one source tree reaches both with no copy and no format translation
# — Pi implements the same Agent Skills standard and ignores the extra frontmatter
# fields the workbench carries.
#
# A skill declaring `agent:` is the one real difference between the harnesses.
# Claude loads that protocol from ~/.claude/agents/ and must not also carry it as
# a skill, so the skill installs to the Pi target only, with the body spliced in.
#
# Supports user overrides: overrides/ai/skills/<name>/ replaces the default,
# overrides/ai/skills/<name>.disabled suppresses it in both harnesses at once.
#
# Only what the workbench installed is ever removed. Both roots are where their
# harness documents personal skills living, so a real directory the workbench
# did not write is reported and kept — see _clear_skill_entry.
step_skills() {
  [[ -d "$SKILLS_SRC_DIR" ]] || { warn "No skills found in $SKILLS_SRC_DIR — skipping"; return; }
  mkdir -p "$CLAUDE_SKILLS_DIR" "$AGENTS_SKILLS_DIR"
  [[ "${WORKBENCH_SYNC:-}" != true ]] \
    && info "Installing skills to $CLAUDE_SKILLS_DIR/ and $AGENTS_SKILLS_DIR/" || true

  local -A layers
  resolve_layers "$SKILLS_SRC_DIR" "$USER_SKILLS_DIR" "*/" layers

  _prune_skills "$CLAUDE_SKILLS_DIR" layers
  _prune_skills "$AGENTS_SKILLS_DIR" layers

  local name source agent
  for name in "${!layers[@]}"; do
    source="${layers[$name]}"

    # skill_agent answers empty for a file that is not there, so the warning is
    # this step's own: an override directory left without a SKILL.md is a mistake
    # worth naming, not a skill that happens to be Claude-only.
    if [[ ! -f "$source/SKILL.md" ]]; then
      warn "No SKILL.md in $source — skipping $name"
      continue
    fi
    agent="$(skill_agent "$source/SKILL.md")"

    if [[ -n "$agent" ]]; then
      # Both layers still provide this name, so the pruning above left the
      # Claude-side entry alone — but a skill that has since declared `agent:`
      # belongs to Claude as an agent file, not as a skill. Removed here rather
      # than in _prune_skills because only this loop has read the frontmatter.
      _clear_skill_entry "$CLAUDE_SKILLS_DIR/$name" || true
      _install_agent_skill "$source" "$AGENTS_SKILLS_DIR/$name" "$agent"
      continue
    fi

    # The inverse case, and unreachable from pruning for the same reason: a skill
    # that has *dropped* `agent:` leaves a real directory in the Pi root, and
    # install_symlink refuses a target holding a real file — under the
    # SYMLINK_MODE=no-prompt that sync sets it warns and skips, so the stale
    # spliced copy would survive every later sync. Symlinks are left for
    # install_symlink to replace in place, which keeps a settled sync silent.
    if [[ -d "$AGENTS_SKILLS_DIR/$name" && ! -L "$AGENTS_SKILLS_DIR/$name" ]]; then
      _clear_skill_entry "$AGENTS_SKILLS_DIR/$name" || true
    fi

    install_symlink "$source" "$CLAUDE_SKILLS_DIR/$name" "$name → claude"
    install_symlink "$source" "$AGENTS_SKILLS_DIR/$name" "$name → agents"
  done
  return 0
}

# sync_skills — runs the skill sync step non-interactively.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
sync_skills() {
  sync_header "skills → $CLAUDE_SKILLS_DIR/ + $AGENTS_SKILLS_DIR/"
  step_skills
}

register_skills_steps() {
  register_step "Agent skills" step_skills
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Skills sync${NC}\n"
  sync_skills
  echo
  success "Skills sync complete!"
fi
