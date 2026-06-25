#!/usr/bin/env bash
# description: Pi coding agent config
# Pi setup steps — sourced by ai/setup.sh.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

PI_HOME="$HOME/.pi"

# step_pi_settings — copies Pi settings.json to ~/.pi/.
# Creates ~/.pi/ if it doesn't exist.
step_pi_settings() {
  mkdir -p "$PI_HOME"
  install_file "$PI_SETTINGS_SRC" "$PI_HOME/settings.json" "Pi settings"
}

# step_pi_skills — deploys Pi agent skills from ai/claude/pi/skills/ to ~/.pi/agent/skills/.
# Replaces the AGENT_PROTOCOL_PLACEHOLDER comment with the agent protocol body from
# the corresponding agent file in ~/.claude/agents/<name>.md (installed by step_claude_agents).
step_pi_skills() {
  [[ -d "$PI_SKILLS_SRC_DIR" ]] || { skip "No Pi skills in $PI_SKILLS_SRC_DIR — skipping"; return; }
  mkdir -p "$PI_SKILLS_DIR"

  local skill_dir name agent_file dest_dir
  for skill_dir in "$PI_SKILLS_SRC_DIR"/*/; do
    [[ -d "$skill_dir" ]] || continue
    name=$(basename "$skill_dir")
    agent_file="$CLAUDE_AGENTS_DIR/${name}.md"
    dest_dir="$PI_SKILLS_DIR/$name"

    if [[ ! -f "$agent_file" ]]; then
      warn "Agent file missing for Pi skill $name: $agent_file — skipping"
      continue
    fi

    mkdir -p "$dest_dir"
    local src="$skill_dir/SKILL.md"
    [[ -f "$src" ]] || continue

    # Extract agent body (skip YAML frontmatter: everything after second ---)
    local agent_body
    agent_body=$(awk 'BEGIN{n=0} /^---$/{n++; if(n==2){found=1; next}} found{print}' "$agent_file")

    # Build output: lines before placeholder, agent body, lines after placeholder
    awk '/<!-- AGENT_PROTOCOL_PLACEHOLDER:/{exit} {print}' "$src" > "$dest_dir/SKILL.md"
    printf '%s\n' "$agent_body" >> "$dest_dir/SKILL.md"
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "Pi skill: $name" || true
  done
}

# _export_pi_config DIR — copies Pi config into DIR for tarball export.
_export_pi_config() {
  local dest="$1"
  mkdir -p "$dest"
  if [[ -f "$PI_SETTINGS_SRC" ]]; then
    cp "$PI_SETTINGS_SRC" "$dest/settings.json"
  fi
}

# sync_pi — runs all Pi sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
sync_pi() {
  sync_header "pi settings → $PI_HOME/"
  step_pi_settings

  sync_header "pi skills → $PI_SKILLS_DIR/"
  step_pi_skills
}

register_pi_steps() {
  register_step "Pi settings" step_pi_settings
  register_step "Pi skills"   step_pi_skills
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Pi sync${NC}\n"
  sync_pi
  echo
  success "Pi sync complete!"
fi
