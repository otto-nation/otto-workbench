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
    symlink_dir "$layer" "$ZSH_CONFIG_DIR/$name" "$ZSH_SNIPPET_GLOB" --prune
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
  if [[ ! -f "$ZSH_LOADER_DST" ]] || ! diff -q "$ZSH_LOADER_SRC" "$ZSH_LOADER_DST" &>/dev/null; then
    cp "$ZSH_LOADER_SRC" "$ZSH_LOADER_DST"
    success "$(basename "$ZSH_LOADER_SRC") updated"
  else
    echo -e "  ${DIM}✓ $(basename "$ZSH_LOADER_SRC")${NC}"
  fi
}

# _zshrc_ensure_integration LOADER_REL — creates ~/.zshrc from template if absent,
# or appends the workbench integration block if the loader source line is missing.
# The workbench owns only one line in ~/.zshrc: the loader source. Everything
# else in the file is yours. Re-running setup never overwrites your config.
_zshrc_ensure_integration() {
  local loader_rel="$1"

  if [[ ! -f "$ZSHRC_FILE" ]]; then
    cp "$ZSH_ZSHRC_TEMPLATE" "$ZSHRC_FILE"
    success "Created $ZSHRC_FILE from template"
    info "Add secrets and machine-specific config to $ENV_LOCAL_FILE (sourced automatically, never committed)"
  elif grep -qF "$loader_rel" "$ZSHRC_FILE" 2>/dev/null; then
    success ".zshrc integration block present — up to date"
  else
    # Append integration block. $loader_rel is expanded now; \$HOME expands at shell-start time.
    cat >> "$ZSHRC_FILE" <<EOF

# ─── WORKBENCH INTEGRATION — added by install.sh ─────────────────────────────
# Loads workbench aliases, tools, and prompt via the config.d loader.
# Machine-specific config goes below this block or in ~/.env.local.

if [[ -f "\$HOME/$loader_rel" ]]; then
  source "\$HOME/$loader_rel"
else
  echo "⚠  workbench not connected — run install.sh to restore" >&2
fi
# ─── END WORKBENCH INTEGRATION ───────────────────────────────────────────────
EOF
    success "Added workbench integration block to $ZSHRC_FILE"
    info "Review the block at the bottom of $ZSHRC_FILE and move it if needed"
  fi
}

# _zshrc_check_duplicates — scans ~/.zshrc for tool initializations now managed
# by workbench snippets and warns if any would be loaded twice.
# Detection patterns are declared in each snippet via '# duplicate-check:',
# so no changes are needed here when snippets are added or removed.
_zshrc_check_duplicates() {
  local -a dupes=()
  local snip pat label rel
  while IFS= read -r snip; do
    pat=$(sed -n 's/^# duplicate-check:[[:space:]]*//p' "$snip" 2>/dev/null | head -1)
    [[ -z "$pat" ]] && continue
    grep -qE "$pat" "$ZSHRC_FILE" 2>/dev/null || continue
    label=$(sed -n 's/^# duplicate-check-label:[[:space:]]*//p' "$snip" 2>/dev/null | head -1)
    [[ -z "$label" ]] && label=$(basename "$snip" .zsh)
    rel="${snip#"$ZSH_CONFIG_SRC_DIR/"}"
    dupes+=("${label}  →  $(basename "$ZSH_CONFIG_SRC_DIR")/${rel}")
  done < <(find "$ZSH_CONFIG_SRC_DIR" -name "$ZSH_SNIPPET_GLOB" 2>/dev/null | sort)

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

# _env_local_bootstrap — creates ~/.env.local from the workbench template when
# absent on a new machine. Never modifies an existing file.
_env_local_bootstrap() {
  if [[ ! -f "$ENV_LOCAL_FILE" ]]; then
    cp "$ENV_LOCAL_TEMPLATE" "$ENV_LOCAL_FILE"
    warn "Created $ENV_LOCAL_FILE from template — review and fill in your values"
  else
    success ".env.local already exists"
  fi
}

# step_zshrc — ensures ~/.zshrc is connected to the workbench loader and warns
# about any tool inits that would now be loaded twice.
step_zshrc() {
  # Derive loader path relative to $HOME — keeps .zshrc portable across accounts.
  local loader_rel="${ZSH_LOADER_DST#"$HOME/"}"
  _zshrc_ensure_integration "$loader_rel"
  _zshrc_check_duplicates
}

# sync_zsh — runs all zsh sync steps non-interactively.
# Includes .zshrc integration so otto-workbench sync repairs a disconnected shell.
# Called automatically by install.sh and otto-workbench sync via the sync_<name> convention.
sync_zsh() {
  echo; info "zsh configs → $ZSH_CONFIG_DIR/"
  mkdir -p "$ZSH_CONFIG_DIR"
  step_zsh
  step_zsh_loader
  install_symlink "$STARSHIP_SRC_FILE" "$STARSHIP_CONFIG_FILE"

  echo; info "ZSH configuration (.zshrc)"
  step_zshrc

  echo; info "Machine secrets template ($ENV_LOCAL_FILE)"
  _env_local_bootstrap
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}ZSH setup${NC}\n"
  sync_zsh
  echo
  success "ZSH setup complete!"
fi
