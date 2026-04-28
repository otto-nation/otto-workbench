#!/usr/bin/env bash
# description: Deploy zsh config layers, loader, and plugins
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
# Re-running is safe — files are updated only when content changes; loader.zsh only copied on change.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# _deploy_zsh_layer SRC DST GLOB — copies .zsh snippets from SRC to DST.
# Skips files with a "# requires-cmd: <cmd>" header when <cmd> is not in PATH.
# Removes previously deployed files for unmet requirements (handles tool uninstall).
# Prunes DST files that no longer exist in SRC.
_deploy_zsh_layer() {
  local src="$1" dst="$2" glob="$3"
  local item name dst_file req_cmd

  for item in "$src"/$glob; do
    [[ -f "$item" ]] || continue
    name=$(basename "$item")
    dst_file="$dst/$name"

    req_cmd=$(sed -n 's/^# requires-cmd:[[:space:]]*//p' "$item" 2>/dev/null | head -1)
    if [[ -n "$req_cmd" ]] && ! command -v "$req_cmd" >/dev/null 2>&1; then
      echo -e "  ${DIM}⊘ $name (requires $req_cmd — install it, then: otto-workbench sync zsh)${NC}"
      # Remove previously deployed file so it doesn't activate a missing tool
      [[ -f "$dst_file" ]] && rm "$dst_file"
      continue
    fi

    install_file "$item" "$dst_file"
  done

  # Prune stale deployed files no longer present in source
  local dst_item
  for dst_item in "$dst"/$glob; do
    [[ -f "$dst_item" ]] || continue
    [[ -e "$src/$(basename "$dst_item")" ]] && continue
    rm "$dst_item"
    echo -e "  ${DIM}⊘ pruned $(basename "$dst_item")${NC}"
  done
}

# step_zsh — auto-discovers all layer directories in ZSH_CONFIG_SRC_DIR and
# deploys their .zsh snippets to matching directories under ZSH_CONFIG_DIR.
# Copies real files (not symlinks) so snippets work from sandboxed apps (e.g. Ghostty/TCC).
# Snippets with "# requires-cmd: <cmd>" are skipped when <cmd> is not installed.
# Safe to re-run: files are updated only when content changes; stale files are pruned.
# Adding a new config.d layer requires no changes here — just create the directory.
step_zsh() {
  local layer name

  for layer in "$ZSH_CONFIG_SRC_DIR"/*/; do
    [[ -d "$layer" ]] || continue
    name=$(basename "$layer")
    mkdir -p "$ZSH_CONFIG_DIR/$name"
    _deploy_zsh_layer "$layer" "$ZSH_CONFIG_DIR/$name" "$ZSH_SNIPPET_GLOB"
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

# step_env_local — ensures ~/.env.local exists and its ENV marker section
# reflects the current registries. User values below ENV-END are never touched.
# The marker section is read-only: fully regenerated from the template each sync.
step_env_local() {
  if [[ ! -f "$ENV_LOCAL_FILE" ]]; then
    cp "$ENV_LOCAL_TEMPLATE" "$ENV_LOCAL_FILE"
    warn "Created $ENV_LOCAL_FILE from template — review and fill in your values"
  fi

  if ! command -v yq >/dev/null 2>&1; then
    success ".env.local exists (yq not found — skipping env var sync)"
    return
  fi

  # Extract the marker section content from the regenerated template
  local new_content
  new_content=$(awk '/# --- ENV-START ---/{found=1;next} /# --- ENV-END ---/{found=0} found{print}' "$ENV_LOCAL_TEMPLATE")

  # Splice it into ~/.env.local between the markers
  local tmp new_content_file
  tmp=$(mktemp)
  new_content_file=$(mktemp)
  printf '%s\n' "$new_content" > "$new_content_file"
  awk -v cf="$new_content_file" '
    /# --- ENV-START ---/ { print; while ((getline line < cf) > 0) print line; skip=1; next }
    /# --- ENV-END ---/   { skip=0 }
    !skip                 { print }
  ' "$ENV_LOCAL_FILE" > "$tmp" && mv "$tmp" "$ENV_LOCAL_FILE"
  rm -f "$new_content_file"

  success ".env.local env section updated"
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
  if command -v starship >/dev/null 2>&1; then
    install_file "$STARSHIP_SRC_FILE" "$STARSHIP_CONFIG_FILE" "starship.toml"
  else
    echo -e "  ${DIM}⊘ starship.toml (requires starship — install it, then: otto-workbench sync zsh)${NC}"
  fi

  echo; info "ZSH configuration (.zshrc)"
  step_zshrc

  echo; info "Environment ($ENV_LOCAL_FILE)"
  # Regenerate the template so it reflects currently installed tools,
  # then splice the ENV section into ~/.env.local.
  if [[ "${WORKBENCH_SKIP_GENERATE:-}" != "1" ]] && command -v yq >/dev/null 2>&1; then
    bash "$WORKBENCH_DIR/bin/generate-tool-context" >/dev/null 2>&1
  fi
  step_env_local

  echo; info "zsh scripts → $LOCAL_BIN_DIR/"
  sync_component_bin "$ZSH_SRC_DIR"
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}ZSH setup${NC}\n"
  sync_zsh
  echo
  success "ZSH setup complete!"
fi
