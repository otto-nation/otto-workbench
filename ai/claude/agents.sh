#!/usr/bin/env bash
# Claude Code agent installation — sourced by ai/claude/steps.sh, never directly.
# All paths come from lib/constants.sh.

# ─── Agents ──────────────────────────────────────────────────────────────────
# Each agent in ai/claude/agents/ declares a tier rather than a model; these
# install it with that tier's model from the machine-wide `models.*`.

# _claude_tier_table VIEW — prints one `tier<TAB>model<TAB>effort` line per
# tier an agent may declare, from `config models` output VIEW: the tier's Claude
# model and level, or `inherit` and no effort for a tier that names none.
_claude_tier_table() {
  jq -r '.claude as $c | $c.tier_names[] as $t
    | [$t, ($c.tiers[$t].model // "inherit"), ($c.tiers[$t].effort // "")] | @tsv' <<< "$1"
}

# _claude_render_agent SOURCE TABLE — prints SOURCE with its frontmatter
# `tier:` line replaced by the `model:` and `effort:` Claude Code reads, looked
# up in TABLE (_claude_tier_table). A `model:` or `effort:` line beside the tier
# is dropped, so the tier is the one answer; only the frontmatter is rewritten,
# and CRLF line endings are read as LF.
#
# The exit status says what it found, since the caller acts differently on each.
# Clear of awk's own 1 and 2, so a failing awk is never read as a verdict:
#   0   rendered
#   10  no `tier:` line — nothing printed; install the file as written
#   11  a `tier:` TABLE does not name (empty, a typo, or an empty TABLE) —
#       rendered as `model: inherit`
# TABLE goes through the environment: BSD awk refuses a newline in a -v value.
_claude_render_agent() {
  TIER_TABLE="$2" awk '
    BEGIN {
      rows = split(ENVIRON["TIER_TABLE"], row, "\n")
      for (i = 1; i <= rows; i++) {
        split(row[i], col, "\t")
        if (col[1] != "") { model[col[1]] = col[2]; effort[col[1]] = col[3] }
      }
    }
    { sub(/\r$/, ""); line[NR] = $0 }
    NR == 1 && $0 == "---" { fm = 1; next }
    fm && !close_at && $0 == "---" { close_at = NR; next }
    fm && !close_at && /^tier:/ {
      tier_at = NR
      tier = $0
      sub(/^tier:[ \t]*/, "", tier)
      sub(/[ \t]+$/, "", tier)
      gsub(/^["\047]|["\047]$/, "", tier)
    }
    END {
      if (!tier_at || !close_at) exit 10
      known = (tier != "" && tier in model)
      m = known ? model[tier] : "inherit"
      e = known ? effort[tier] : ""
      for (i = 1; i <= NR; i++) {
        if (i > 1 && i < close_at && line[i] ~ /^(model|effort):/) continue
        if (i == tier_at) {
          print "model: " m
          if (e != "") print "effort: " e
          continue
        }
        print line[i]
      }
      exit known ? 0 : 11
    }' "$1"
}

# step_claude_agents — copies each agent markdown file into ~/.claude/agents/.
# Supports user overrides: user/ai/claude/agents/<name>.md replaces the default,
# user/ai/claude/agents/<name>.disabled suppresses it entirely.
#
# An agent whose frontmatter declares `tier: deep|standard|light` is installed
# with that tier's model from the machine-wide `models.*` (see
# _claude_render_agent), so a subagent dispatched without a model of its own
# runs on its tier rather than on whatever the session is. One without a tier is
# copied as written; one naming a tier that does not exist is installed as
# `inherit`, with a warning.
#
# A config that cannot be read leaves every installed tiered agent as it is
# rather than demoting it to `inherit` — the same rule the settings step keeps
# for the tier aliases. An agent installed for the first time then goes in as
# `inherit`. The view is fetched here rather than handed over by the settings
# step, because each step must run on its own.
step_claude_agents() {
  [[ -d "$CLAUDE_AGENTS_SRC_DIR" ]] || { warn "No agents found in $CLAUDE_AGENTS_SRC_DIR — skipping"; return; }
  mkdir -p "$CLAUDE_AGENTS_DIR"
  [[ "${WORKBENCH_SYNC:-}" != true ]] && info "Installing Claude Code agents to $CLAUDE_AGENTS_DIR/" || true

  local -A layers
  resolve_layers "$CLAUDE_AGENTS_SRC_DIR" "$USER_AGENTS_DIR" "*.md" layers

  # Prune agents in target that are no longer in either layer
  local item
  for item in "$CLAUDE_AGENTS_DIR"/*.md; do
    [[ -e "$item" || -L "$item" ]] || continue
    local name
    name=$(basename "$item")
    if [[ -z "${layers[$name]+set}" ]]; then
      rm "$item"
      [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}⊘ pruned ${name%.md}${NC}" || true
    fi
  done

  local view table="" config_ok=true
  if ! view=$(wb_config_harness_models 2> /dev/null) || ! table=$(_claude_tier_table "$view"); then
    config_ok=false
    warn "Tiered Claude Code agents left as they were installed — fix the models section of config.yml"
  fi

  # Install from merged layers
  local name source label target rendered rc
  for name in "${!layers[@]}"; do
    source="${layers[$name]}"
    label="${name%.md}"
    target="$CLAUDE_AGENTS_DIR/$name"
    rc=0
    rendered=$(_claude_render_agent "$source" "$table") || rc=$?
    case "$rc" in
      10) install_file "$source" "$target" "$label"; continue ;;
      0|11) ;;
      *) err "Failed to render agent $label"; return 1 ;;
    esac
    if [[ "$config_ok" == false && -f "$target" ]]; then
      continue
    fi
    if [[ "$rc" -eq 11 && "$config_ok" == true ]]; then
      warn "Agent $label declares a tier models.* does not have — one of: $(cut -f1 <<< "$table" | paste -sd, - | sed 's/,/, /g') — so it inherits the session's model"
    fi
    install_text "$rendered" "$target" "$label"
  done
}
