#!/usr/bin/env bash
# herdr-app layout install — sourced by ai/herdr/steps.sh. Derives personal
# layouts from Herdr's installed main.json via committed jq programs in
# HERDR_APP_LAYOUTS_SRC_DIR. The step installs into
# HERDR_UNTRUSTED_APP_DIR too, so both windows carry the same layouts.

# _herdr_layout_cksum FILE — POSIX cksum field 1 of FILE, or empty when missing.
_herdr_layout_cksum() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  cksum "$file" | awk '{print $1}'
  return 0
}

# _herdr_layout_load_manifest FILE NAMEREF — fills the nameref assoc array from
# a name<TAB>cksum manifest. Missing FILE is an empty map.
_herdr_layout_load_manifest() {
  local file="$1" name sum
  local -n __old=$2
  [[ -f "$file" ]] || return 0
  while IFS=$'\t' read -r name sum || [[ -n "$name" ]]; do
    [[ -n "$name" ]] || continue
    __old["$name"]="$sum"
  done < "$file"
  return 0
}

# _herdr_layout_commit_manifest MANIFEST TMP — sorts TMP and replaces MANIFEST
# when they differ. TMP is always consumed.
_herdr_layout_commit_manifest() {
  local manifest="$1" tmp="$2"
  if ! LC_ALL=C sort -o "$tmp" "$tmp"; then
    rm -f "$tmp"
    warn "Could not sort $manifest"
    return 1
  fi
  if [[ -f "$manifest" ]] && cmp -s "$tmp" "$manifest"; then
    rm -f "$tmp"
    return 0
  fi
  mkdir -p "$(dirname "$manifest")" || {
    rm -f "$tmp"
    warn "Could not create $(dirname "$manifest")"
    return 1
  }
  mv "$tmp" "$manifest" || { rm -f "$tmp"; warn "Could not write $manifest"; return 1; }
  return 0
}

# _herdr_layout_write DEST JSON CHANGED_NAMEREF — writes JSON onto DEST when
# content differs. Sets the nameref when it wrote.
_herdr_layout_write() {
  local dest="$1" json="$2"
  local -n __changed=$3
  if [[ -f "$dest" ]] && printf '%s\n' "$json" | cmp -s "$dest" -; then
    return 0
  fi
  wb_write_json_file "$dest" "$json" || { warn "Could not write $dest"; return 1; }
  __changed=1
  return 0
}

# _herdr_layout_keep NAME SUM MAN_OUT — records NAME's existing checksum.
_herdr_layout_keep() {
  local name="$1" sum="$2" man_out="$3"
  [[ -n "$sum" ]] && printf '%s\t%s\n' "$name" "$sum" >> "$man_out"
  return 0
}

# _herdr_layout_protected DEST NAME OLD_SUM OUT MAN_OUT HOME — 0 when DEST
# must stay (hand edit, foreign file, or a shipped layout). Records the
# checksum that should remain in the manifest, or nothing. 1 means write.
_herdr_layout_protected() {
  local dest="$1" name="$2" old_sum="$3" out="$4" man_out="$5" home="$6"
  local dest_sum installed="$home/installed.json"
  if [[ -f "$installed" ]] && jq -e --arg k "layout:$name" 'has($k)' "$installed" >/dev/null; then
    warn "herdr-app layout $name: $dest is a shipped herdr-app layout — left as is"
    _herdr_layout_keep "$name" "$old_sum" "$man_out"
    return 0
  fi
  [[ -f "$dest" ]] || return 1
  dest_sum=$(_herdr_layout_cksum "$dest")
  if [[ -n "$old_sum" && "$dest_sum" != "$old_sum" ]]; then
    warn "herdr-app layout $name: $dest differs from the workbench copy — left as is; delete it and re-run otto-workbench sync to restore it"
    _herdr_layout_keep "$name" "$old_sum" "$man_out"
    return 0
  fi
  if [[ -z "$old_sum" ]]; then
    if printf '%s\n' "$out" | cmp -s "$dest" -; then
      printf '%s\t%s\n' "$name" "$dest_sum" >> "$man_out"
      return 0
    fi
    warn "herdr-app layout $name: $dest exists without a workbench manifest and differs from the workbench copy — left as is"
    return 0
  fi
  return 1
}

# _herdr_layout_install_one SRC HOME NAME OLD_SUM MAN_OUT — derives NAME.json
# from HOME/layouts/main.json. Non-fatal.
_herdr_layout_install_one() {
  local src="$1" home="$2" name="$3" old_sum="$4" man_out="$5"
  local dest="$home/layouts/$name.json" base="$home/layouts/main.json"
  local out changed=0
  if [[ ! -f "$base" ]]; then
    info "herdr-app layout $name: start herdr-app once, then re-run sync"
    _herdr_layout_keep "$name" "$old_sum" "$man_out"
    return 0
  fi
  if ! out=$(jq -f "$src" "$base"); then
    warn "herdr-app layout $name: could not derive from $base"
    _herdr_layout_keep "$name" "$old_sum" "$man_out"
    return 0
  fi
  mkdir -p "$home/layouts" || { warn "Could not create $home/layouts"; return 0; }
  if _herdr_layout_protected "$dest" "$name" "$old_sum" "$out" "$man_out" "$home"; then
    return 0
  fi
  [[ -f "$dest" ]] || HERDR_APP_LAYOUTS_NEWLY_INSTALLED+=("$name")
  if ! _herdr_layout_write "$dest" "$out" changed; then
    _herdr_layout_keep "$name" "$old_sum" "$man_out"
    return 0
  fi
  printf '%s\t%s\n' "$name" "$(_herdr_layout_cksum "$dest")" >> "$man_out"
  [[ "$changed" -eq 1 ]] && success "herdr-app layout $name"
  return 0
}

# _herdr_app_install_layouts [HOME] [MANIFEST] — for each jq program under
# HERDR_APP_LAYOUTS_SRC_DIR, writes HOME/layouts/<name>.json (default
# HERDR_APP_DIR) and records name<TAB>cksum in MANIFEST (default
# HERDR_APP_LAYOUTS_MANIFEST). Non-fatal: always returns 0.
#
# HERDR_APP_LAYOUTS_NEWLY_INSTALLED is a global array this function resets,
# then fills with layout names that had no dest file before this run.
_herdr_app_install_layouts() {
  local home="${1:-$HERDR_APP_DIR}"
  local manifest="${2:-$HERDR_APP_LAYOUTS_MANIFEST}"
  local src_root="$HERDR_APP_LAYOUTS_SRC_DIR" src name tmp
  local -A old=()
  HERDR_APP_LAYOUTS_NEWLY_INSTALLED=()
  [[ -d "$src_root" ]] || return 0
  mkdir -p "$(dirname "$manifest")" || {
    warn "Could not create $(dirname "$manifest")"
    return 0
  }
  _herdr_layout_load_manifest "$manifest" old
  tmp=$(mktemp "$manifest.XXXXXX") || { warn "Could not write $manifest"; return 0; }
  for src in "$src_root"/*.jq; do
    [[ -f "$src" ]] || continue
    name=$(basename "$src" .jq)
    _herdr_layout_install_one "$src" "$home" "$name" "${old[$name]-}" "$tmp"
    unset "old[$name]"
  done
  for name in "${!old[@]}"; do
    printf '%s\t%s\n' "$name" "${old[$name]}" >> "$tmp"
  done
  _herdr_layout_commit_manifest "$manifest" "$tmp" || return 0
  return 0
}
