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

# _skill_agent SKILL_FILE — prints the agent name the skill's frontmatter declares.
#
# Prints nothing for a skill with no `agent:` field, which is every skill that
# reads the same under both harnesses.
_skill_agent() {
  awk 'NR==1 && /^---$/ { in_fm=1; next }
       in_fm && /^---$/ { exit }
       in_fm && /^agent:[[:space:]]/ {
         sub(/^agent:[[:space:]]*/, ""); gsub(/["\047]/, ""); print; exit
       }' "$1"
}

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

# _install_agent_skill SOURCE_DIR TARGET_DIR AGENT — writes a real SKILL.md with
# the agent's protocol body spliced in place of the placeholder comment.
#
# A real file rather than a symlink because the content does not exist on disk
# anywhere: it is the skill's frontmatter joined to a body that lives in the
# agent file. Rewritten wholesale on every run so a re-sync cannot append.
_install_agent_skill() {
  local source_dir="$1" target_dir="$2" agent="$3"
  local agent_file
  agent_file="$(_resolve_agent_file "$agent")"

  if [[ ! -f "$agent_file" ]]; then
    warn "Agent file missing for skill $(basename "$source_dir"): $agent_file — skipping"
    return 0
  fi

  rm -rf "$target_dir"
  mkdir -p "$target_dir"
  awk -v marker="$AGENT_PROTOCOL_PLACEHOLDER_MARKER" \
    'index($0, marker) { exit } { print }' \
    "$source_dir/SKILL.md" > "$target_dir/SKILL.md"
  awk 'BEGIN { n = 0 }
       /^---$/ { n++; if (n == 2) { found = 1; next } }
       found { print }' "$agent_file" >> "$target_dir/SKILL.md"
}

# _prune_skills TARGET_DIR LAYERS_VAR — removes entries in TARGET_DIR that
# neither the default nor the override layer provides.
#
# The __ prefix on the nameref is required: a local sharing the caller's variable
# name would shadow the nameref's target and the assignment would silently land
# in this scope instead.
_prune_skills() {
  local target="$1"
  local -n __skill_layers=$2
  local item name
  for item in "$target"/*/; do
    [[ -L "${item%/}" || -d "$item" ]] || continue
    name=$(basename "$item")
    if [[ -z "${__skill_layers[$name]+set}" ]]; then
      rm -rf "${item%/}"
      [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}⊘ pruned $name${NC}" || true
    fi
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
    agent="$(_skill_agent "$source/SKILL.md")"

    if [[ -n "$agent" ]]; then
      # Both layers still provide this name, so the pruning above left the
      # Claude-side entry alone — but a skill that has since declared `agent:`
      # belongs to Claude as an agent file, not as a skill. Removed here rather
      # than in _prune_skills because only this loop has read the frontmatter.
      rm -rf "${CLAUDE_SKILLS_DIR:?}/$name"
      _install_agent_skill "$source" "$AGENTS_SKILLS_DIR/$name" "$agent"
      continue
    fi

    install_symlink "$source" "$CLAUDE_SKILLS_DIR/$name" "$name"
    install_symlink "$source" "$AGENTS_SKILLS_DIR/$name" "$name"
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
