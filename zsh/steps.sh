#!/bin/bash
# ZSH configuration setup.
#
# Usage: bash zsh/steps.sh
#        (also sourced by install.sh and bin/otto-workbench for step functions)
#
# What it does:
#   1. Auto-discovers layer directories from zsh/config.d/ and deploys snippets
#      to ~/.config/zsh/config.d/<layer>/ — no changes needed here when layers are added
#   2. Copies loader.zsh as a real file (survives repo move/delete)
#   3. Ensures ~/.zshrc contains the workbench integration block
#   4. Warns about tool initializations in ~/.zshrc now managed by snippets
#
# Adding a new snippet layer:
#   1. Create zsh/config.d/<layer>/ and add .zsh snippet files
#   2. Add a _wb_load <layer> call to zsh/config.d/loader.zsh at the right position
#   step_zsh picks up the new directory automatically — no changes needed here.
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

# step_zsh — auto-discovers all layer directories in ZSH_CONFIG_SRC_DIR and
# deploys their .zsh snippets to matching directories under ZSH_CONFIG_DIR.
# Safe to re-run: symlinks are updated silently; stale symlinks are pruned.
# Adding a new config.d layer requires no changes here — just create the directory.
step_zsh() {
  local layer name

  for layer in "$ZSH_CONFIG_SRC_DIR"/*/; do
    [[ -d "$layer" ]] || continue
    name=$(basename "$layer")
    mkdir -p "$ZSH_CONFIG_DIR/$name"
    symlink_dir "$layer" "$ZSH_CONFIG_DIR/$name" "*.zsh" --prune
  done

  # Migration: prune stale aliases-*.zsh symlinks left at the config.d root
  # from before the layer-subdirectory restructure. Safe to remove once all
  # machines have been re-synced past that change.
  local stale
  for stale in "$ZSH_CONFIG_DIR"/aliases-*.zsh; do
    [[ -L "$stale" ]] || continue
    rm "$stale"
    echo -e "  ${DIM}⊘ pruned $(basename "$stale") (moved to aliases/)${NC}"
  done
}

# step_zsh_loader — copies loader.zsh as a real file (never a symlink) so the
# shell continues to work even if the workbench repo is moved or deleted.
# Only copies when content has changed; no-op otherwise.
# Note: loader.zsh controls layer load order — its _wb_load lines must be
# maintained manually when adding new layers (order matters: framework first, prompt last).
step_zsh_loader() {
  local loader_dst="$ZSH_CONFIG_DIR/loader.zsh"
  if [[ ! -f "$loader_dst" ]] || ! diff -q "$ZSH_CONFIG_SRC_DIR/loader.zsh" "$loader_dst" &>/dev/null; then
    cp "$ZSH_CONFIG_SRC_DIR/loader.zsh" "$loader_dst"
    success "loader.zsh updated"
  else
    echo -e "  ${DIM}✓ loader.zsh${NC}"
  fi
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
  step_zsh_loader
  install_symlink "$STARSHIP_SRC_FILE" "$STARSHIP_CONFIG_FILE"
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}ZSH setup${NC}\n"

  mkdir -p "$ZSH_CONFIG_DIR"

  echo; info "zsh configs → $ZSH_CONFIG_DIR/"
  step_zsh
  step_zsh_loader

  echo; info "ZSH configuration (.zshrc)"
  step_zshrc

  echo
  success "ZSH setup complete!"
fi
