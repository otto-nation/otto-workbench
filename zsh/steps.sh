#!/bin/bash
# ZSH configuration setup.
#
# Usage: bash zsh/steps.sh
#        (also sourced by install.sh and bin/otto-workbench for step functions)
#
# What it does:
#   1. Deploys workbench snippets to ~/.config/zsh/config.d/{framework,tools,aliases,prompt}/
#   2. Copies loader.zsh as a real file (survives repo move/delete)
#   3. Ensures ~/.zshrc contains the workbench integration block
#   4. Warns about any tool initializations in ~/.zshrc that are now managed by
#      snippets and would run twice — patterns are declared in each snippet via
#      '# duplicate-check:' so detection stays in sync with the snippets themselves
#
# Re-running is safe — symlinks are updated silently; loader.zsh only copied on change.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_zsh — deploys the zsh config.d directory structure and all workbench-managed
# snippets. Safe to re-run: symlinks are updated silently, and loader.zsh is only
# copied when its content has changed.
#
# Layout deployed to $ZSH_CONFIG_DIR (~/.config/zsh/config.d/):
#   framework/  — shell framework snippets (oh-my-zsh, etc.)
#   tools/      — version manager snippets (pyenv, nvm, sdkman, etc.)
#   aliases/    — command shortcut snippets — always deployed
#   prompt/     — prompt snippets (starship)
#   loader.zsh  — load-order definition; copied as a real file (not a symlink)
#                 so it survives if the workbench repo is moved or deleted
#
# All snippets guard against missing tools — it is safe to deploy them all
# regardless of which tools are installed on this machine.
step_zsh() {
  # Create layer directories
  mkdir -p \
    "$ZSH_CONFIG_DIR/framework" \
    "$ZSH_CONFIG_DIR/tools" \
    "$ZSH_CONFIG_DIR/aliases" \
    "$ZSH_CONFIG_DIR/prompt"

  # Deploy snippets — all layers are symlinked from the workbench repo.
  # Each snippet is self-guarding (returns 0 if its tool is not installed).
  symlink_dir "$ZSH_CONFIG_SRC_DIR/framework" "$ZSH_CONFIG_DIR/framework" "*.zsh" --prune
  symlink_dir "$ZSH_CONFIG_SRC_DIR/tools"     "$ZSH_CONFIG_DIR/tools"     "*.zsh" --prune
  symlink_dir "$ZSH_CONFIG_SRC_DIR/aliases"   "$ZSH_CONFIG_DIR/aliases"   "*.zsh" --prune
  symlink_dir "$ZSH_CONFIG_SRC_DIR/prompt"    "$ZSH_CONFIG_DIR/prompt"    "*.zsh" --prune

  # Copy loader.zsh as a real file — never a symlink. This ensures the shell
  # continues to work even if the workbench repo is moved or deleted.
  local loader_dst="$ZSH_CONFIG_DIR/loader.zsh"
  if [[ ! -f "$loader_dst" ]] || ! diff -q "$ZSH_CONFIG_SRC_DIR/loader.zsh" "$loader_dst" &>/dev/null; then
    cp "$ZSH_CONFIG_SRC_DIR/loader.zsh" "$loader_dst"
    success "loader.zsh updated"
  else
    echo -e "  ${DIM}✓ loader.zsh${NC}"
  fi

  # Migration: prune stale aliases-*.zsh symlinks from the config.d root.
  # These were the pre-restructure locations; they are now in aliases/.
  local stale
  for stale in "$ZSH_CONFIG_DIR"/aliases-*.zsh; do
    [[ -L "$stale" ]] || continue
    rm "$stale"
    echo -e "  ${DIM}⊘ pruned $(basename "$stale") (moved to aliases/)${NC}"
  done
}

# step_zshrc — ensures ~/.zshrc exists and contains the workbench integration
# block. On a new machine, copies the template. On an existing machine, checks
# for the loader source line and appends the integration block if it is absent.
#
# After ensuring the integration block is present, scans for tool initializations
# that are now managed by workbench snippets and warns if any are found — these
# would be loaded twice on every shell start. Detection patterns are declared
# in each snippet via '# duplicate-check:', so no changes are needed here when
# snippets are added or removed.
#
# The workbench owns only one line in ~/.zshrc: the loader source. Everything
# else in the file is yours. Re-running setup never overwrites your config.
step_zshrc() {
  local marker="config.d/loader.zsh"

  if [[ ! -f "$ZSHRC_FILE" ]]; then
    cp "$ZSH_ZSHRC_TEMPLATE" "$ZSHRC_FILE"
    success "Created $ZSHRC_FILE from template"
    info "Add secrets and machine-specific config to $ENV_LOCAL_FILE (sourced automatically, never committed)"
  elif grep -qF "$marker" "$ZSHRC_FILE" 2>/dev/null; then
    success ".zshrc integration block present — up to date"
  else
    # Append the integration block to the existing file
    cat >> "$ZSHRC_FILE" <<'EOF'

# ─── WORKBENCH INTEGRATION — added by install.sh ─────────────────────────────
# Loads workbench aliases, tools, and prompt via the config.d loader.
# Machine-specific config goes below this block or in ~/.env.local.

if [[ -f "$HOME/.config/zsh/config.d/loader.zsh" ]]; then
  source "$HOME/.config/zsh/config.d/loader.zsh"
else
  echo "⚠  workbench not connected — run install.sh to restore" >&2
fi
# ─── END WORKBENCH INTEGRATION ───────────────────────────────────────────────
EOF
    success "Added workbench integration block to $ZSHRC_FILE"
    info "Review the block at the bottom of $ZSHRC_FILE and move it if needed"
  fi

  # ── Duplicate detection ────────────────────────────────────────────────────
  # Reads detection patterns directly from snippet files — no hardcoding here.
  # Any snippet can opt in by adding these metadata comments to its header:
  #
  #   # duplicate-check: <egrep-pattern>   — pattern to look for in ~/.zshrc
  #   # duplicate-check-label: <label>     — optional display name (default: filename)
  #
  # Adding a new snippet with a duplicate-check line automatically enrolls it.
  local -a dupes=()
  local snip pat label rel
  while IFS= read -r snip; do
    pat=$(sed -n 's/^# duplicate-check:[[:space:]]*//p' "$snip" 2>/dev/null | head -1)
    [[ -z "$pat" ]] && continue
    grep -qE "$pat" "$ZSHRC_FILE" 2>/dev/null || continue
    label=$(sed -n 's/^# duplicate-check-label:[[:space:]]*//p' "$snip" 2>/dev/null | head -1)
    [[ -z "$label" ]] && label=$(basename "$snip" .zsh)
    rel="${snip#"$ZSH_CONFIG_SRC_DIR/"}"
    dupes+=("${label}  →  config.d/${rel}")
  done < <(find "$ZSH_CONFIG_SRC_DIR" -name '*.zsh' 2>/dev/null | sort)

  if [[ ${#dupes[@]} -gt 0 ]]; then
    echo
    warn "$ZSHRC_FILE has tool setup now managed by workbench snippets:"
    local d
    for d in "${dupes[@]}"; do
      echo -e "  ${DIM}  • $d${NC}"
    done
    echo -e "  ${DIM}  These will load twice. Remove the old lines: \${EDITOR:-nano} $ZSHRC_FILE${NC}"
  fi
}

# sync_zsh — runs all zsh sync steps non-interactively, including starship.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_zsh() {
  echo; info "zsh configs → $ZSH_CONFIG_DIR/"
  mkdir -p "$ZSH_CONFIG_DIR"
  step_zsh
  install_symlink "$STARSHIP_SRC_FILE" "$STARSHIP_CONFIG_FILE"
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}ZSH setup${NC}\n"

  mkdir -p "$ZSH_CONFIG_DIR"

  echo; info "zsh configs → $ZSH_CONFIG_DIR/"
  step_zsh

  echo; info "ZSH configuration (.zshrc)"
  step_zshrc

  echo
  success "ZSH setup complete!"
fi
