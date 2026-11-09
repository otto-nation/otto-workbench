#!/usr/bin/env bash
# herdr-app mod install — sourced by ai/herdr/steps.sh. Copies workbench mods
# into HERDR_APP_DIR/mods as real directories with a per-file checksum
# manifest. The step installs into HERDR_UNTRUSTED_APP_DIR too, so both
# windows carry identical mods.

# _herdr_mod_cksum FILE — POSIX cksum field 1 of FILE, or empty when FILE is missing.
_herdr_mod_cksum() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  cksum "$file" | awk '{print $1}'
  return 0
}

# _herdr_mod_skip_rel REL — true when REL is not a workbench-owned mod path:
# a dotfile, a node_modules path, gpui-kit.d.ts, or jsconfig.json.
_herdr_mod_skip_rel() {
  local rel="$1" rest="$1" part
  [[ -n "$rel" ]] || return 0
  while [[ -n "$rest" ]]; do
    part="${rest%%/*}"
    if [[ "$rest" == */* ]]; then rest="${rest#*/}"; else rest=""; fi
    [[ "$part" == .* || "$part" == node_modules ]] && return 0
    [[ "$part" == gpui-kit.d.ts || "$part" == jsconfig.json ]] && return 0
  done
  return 1
}

# _herdr_mod_list_files ROOT — relative paths of installable files under ROOT.
_herdr_mod_list_files() {
  local root="$1" f rel listing
  [[ -d "$root" ]] || return 0
  listing=$(find "$root" -type f | LC_ALL=C sort) || true
  while IFS= read -r f || [[ -n "$f" ]]; do
    [[ -n "$f" ]] || continue
    rel="${f#"$root"/}"
    if _herdr_mod_skip_rel "$rel"; then
      continue
    fi
    printf '%s\n' "$rel"
  done <<< "$listing"
  return 0
}

# _herdr_mod_source_rels SRC_MOD — relative paths installed for this mod:
# SRC_MOD's files plus every shared `_*` tree (`_lib/`, `_ui/`), each prefixed
# with its directory name.
_herdr_mod_source_rels() {
  local src_mod="$1" rel listing shared name shared_listing
  listing=$(_herdr_mod_list_files "$src_mod")
  for shared in "$HERDR_APP_MODS_SRC_DIR"/_*/; do
    [[ -d "$shared" ]] || continue
    name=$(basename "$shared")
    shared_listing=$(_herdr_mod_list_files "${shared%/}")
    while IFS= read -r rel || [[ -n "$rel" ]]; do
      [[ -n "$rel" ]] || continue
      listing+=$'\n'"$name/$rel"
    done <<< "$shared_listing"
  done
  printf '%s\n' "$listing" | LC_ALL=C sort -u | sed '/^$/d'
  return 0
}

# _herdr_mod_src_for SRC_MOD REL — source path that supplies REL in SRC_MOD.
# A REL whose first segment is a shared `_*` directory of HERDR_APP_MODS_SRC_DIR
# comes from there, not the mod folder.
_herdr_mod_src_for() {
  local src_mod="$1" rel="$2" top="${2%%/*}"
  if [[ "$rel" == _*/* && -d "$HERDR_APP_MODS_SRC_DIR/$top" ]]; then
    printf '%s\n' "$HERDR_APP_MODS_SRC_DIR/$rel"
    return 0
  fi
  printf '%s\n' "$src_mod/$rel"
  return 0
}

# _herdr_mod_write_file SRC DEST CHANGED_NAMEREF — copies SRC onto DEST when
# content differs (install_file / cmp -s semantics). Sets the nameref when it wrote.
_herdr_mod_write_file() {
  local src="$1" dest="$2" parent
  local -n __changed=$3
  parent=$(dirname "$dest")
  mkdir -p "$parent" || { warn "Could not create $parent"; return 1; }
  if [[ -L "$dest" ]]; then
    rm "$dest" || { warn "Could not replace symlink $dest"; return 1; }
  fi
  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
    return 0
  fi
  cp "$src" "$dest" || { warn "Could not write $dest"; return 1; }
  __changed=1
  return 0
}

# _herdr_mod_sync_file SRC DEST REL OLD_SUM MANIFEST_OUT CHANGED_NAMEREF —
# writes SRC to DEST unless DEST is a hand edit (checksum differs from OLD_SUM).
# Appends REL and the checksum that should stay in the manifest to MANIFEST_OUT.
_herdr_mod_sync_file() {
  local src="$1" dest="$2" rel="$3" old_sum="$4" man_out="$5" dest_sum
  local -n __changed=$6
  dest_sum=$(_herdr_mod_cksum "$dest")
  if [[ -n "$dest_sum" && -n "$old_sum" && "$dest_sum" != "$old_sum" ]]; then
    warn "herdr-app mod $rel: $dest differs from the workbench copy — left as is; delete it and re-run otto-workbench sync to restore it"
    printf '%s\t%s\n' "$rel" "$old_sum" >> "$man_out"
    return 0
  fi
  _herdr_mod_write_file "$src" "$dest" "$6" || return 0
  printf '%s\t%s\n' "$rel" "$(_herdr_mod_cksum "$dest")" >> "$man_out"
  return 0
}

# _herdr_mod_remove_stale DEST REL OLD_SUM MANIFEST_OUT CHANGED_NAMEREF —
# drops a manifest path that is gone from the source, unless DEST is a hand edit.
_herdr_mod_remove_stale() {
  local dest="$1" rel="$2" old_sum="$3" man_out="$4" dest_sum
  local -n __changed=$5
  dest_sum=$(_herdr_mod_cksum "$dest")
  if [[ -n "$dest_sum" && -n "$old_sum" && "$dest_sum" != "$old_sum" ]]; then
    warn "herdr-app mod $rel: $dest was edited — left as is (source removed); delete it and re-run otto-workbench sync to drop it"
    printf '%s\t%s\n' "$rel" "$old_sum" >> "$man_out"
    return 0
  fi
  if [[ -f "$dest" || -L "$dest" ]]; then
    rm -f "$dest"
    __changed=1
  fi
  return 0
}

# _herdr_mod_can_adopt SRC_MOD DEST — true when every source path is present
# under DEST and byte-identical to the workbench copy. Extra files are ignored.
_herdr_mod_can_adopt() {
  local src_mod="$1" dest="$2" rel src rels
  rels=$(_herdr_mod_source_rels "$src_mod")
  while IFS= read -r rel || [[ -n "$rel" ]]; do
    [[ -n "$rel" ]] || continue
    src=$(_herdr_mod_src_for "$src_mod" "$rel")
    [[ -f "$dest/$rel" ]] || return 1
    cmp -s "$src" "$dest/$rel" || return 1
  done <<< "$rels"
  return 0
}

# _herdr_mod_write_adopt_manifest SRC_MOD DEST — writes DEST/.workbench-install
# from the source checksums without rewriting any payload file.
_herdr_mod_write_adopt_manifest() {
  local src_mod="$1" dest="$2" tmp rel src man rels
  man="$dest/.workbench-install"
  tmp=$(mktemp "$man.XXXXXX") || return 1
  rels=$(_herdr_mod_source_rels "$src_mod")
  while IFS= read -r rel || [[ -n "$rel" ]]; do
    [[ -n "$rel" ]] || continue
    src=$(_herdr_mod_src_for "$src_mod" "$rel")
    printf '%s\t%s\n' "$rel" "$(_herdr_mod_cksum "$src")" >> "$tmp"
  done <<< "$rels"
  if ! LC_ALL=C sort -o "$tmp" "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$man" || { rm -f "$tmp"; return 1; }
  return 0
}

# _herdr_mod_adopt_or_skip SRC_MOD DEST NAME — a target dir with no manifest is
# adopted when identical on every source path, otherwise left as the person's mod.
_herdr_mod_adopt_or_skip() {
  local src_mod="$1" dest="$2" name="$3"
  if _herdr_mod_can_adopt "$src_mod" "$dest"; then
    _herdr_mod_write_adopt_manifest "$src_mod" "$dest" \
      || { warn "herdr-app mod $name: could not write $dest/.workbench-install"; return 0; }
    success "herdr-app mod $name"
    return 0
  fi
  warn "herdr-app mod $name: $dest exists without a workbench manifest and differs from the workbench copy — left as is"
  return 0
}

# _herdr_mod_load_manifest FILE NAMEREF — fills the nameref assoc array from a
# relpath<TAB>cksum manifest. Missing FILE is an empty map.
_herdr_mod_load_manifest() {
  local file="$1" rel old_sum
  local -n __old=$2
  [[ -f "$file" ]] || return 0
  while IFS=$'\t' read -r rel old_sum || [[ -n "$rel" ]]; do
    [[ -n "$rel" ]] || continue
    __old["$rel"]="$old_sum"
  done < "$file"
  return 0
}

# _herdr_mod_commit_manifest MANIFEST TMP CHANGED_NAMEREF — sorts TMP and
# replaces MANIFEST when they differ. TMP is always consumed.
_herdr_mod_commit_manifest() {
  local manifest="$1" tmp="$2"
  local -n __changed=$3
  if ! LC_ALL=C sort -o "$tmp" "$tmp"; then
    rm -f "$tmp"
    warn "Could not sort $manifest"
    return 1
  fi
  if [[ -f "$manifest" ]] && cmp -s "$tmp" "$manifest"; then
    rm -f "$tmp"
    return 0
  fi
  mv "$tmp" "$manifest" || { rm -f "$tmp"; warn "Could not write $manifest"; return 1; }
  __changed=1
  return 0
}

# _herdr_mod_sync_dir SRC_MOD DEST NAME — installs SRC_MOD into DEST using the
# per-file manifest at DEST/.workbench-install. Non-fatal.
_herdr_mod_sync_dir() {
  local src_mod="$1" dest="$2" name="$3"
  local manifest="$dest/.workbench-install" tmp rel src changed=0 rels
  local -A old=()
  mkdir -p "$dest" || { warn "Could not create $dest"; return 0; }
  _herdr_mod_load_manifest "$manifest" old
  tmp=$(mktemp "$manifest.XXXXXX") || { warn "Could not write $manifest"; return 0; }
  rels=$(_herdr_mod_source_rels "$src_mod")
  while IFS= read -r rel || [[ -n "$rel" ]]; do
    [[ -n "$rel" ]] || continue
    src=$(_herdr_mod_src_for "$src_mod" "$rel")
    _herdr_mod_sync_file "$src" "$dest/$rel" "$rel" "${old[$rel]-}" "$tmp" changed
    unset "old[$rel]"
  done <<< "$rels"
  for rel in "${!old[@]}"; do
    _herdr_mod_remove_stale "$dest/$rel" "$rel" "${old[$rel]}" "$tmp" changed
  done
  _herdr_mod_commit_manifest "$manifest" "$tmp" changed || return 0
  if [[ "$changed" -eq 1 ]]; then
    success "herdr-app mod $name"
  fi
  return 0
}

# _herdr_mod_install_one SRC_MOD DEST NAME — installs one mod directory.
# A symlink DEST is left alone; a dir with no manifest is adopted or skipped.
# Appends NAME to HERDR_APP_MODS_NEWLY_INSTALLED when this run writes the
# dest's .workbench-install and none was there before (fresh copy or adopt).
_herdr_mod_install_one() {
  local src_mod="$1" dest="$2" name="$3" had_manifest=0
  [[ -f "$dest/.workbench-install" ]] && had_manifest=1
  if [[ -L "$dest" ]]; then
    warn "herdr-app mod $name: $dest is a symlink — left as is"
    return 0
  fi
  if [[ -d "$dest" && ! -f "$dest/.workbench-install" ]]; then
    _herdr_mod_adopt_or_skip "$src_mod" "$dest" "$name"
  else
    _herdr_mod_sync_dir "$src_mod" "$dest" "$name"
  fi
  if [[ "$had_manifest" -eq 0 && -f "$dest/.workbench-install" ]]; then
    HERDR_APP_MODS_NEWLY_INSTALLED+=("$name")
  fi
  return 0
}

# _herdr_app_install_mods [HOME] — copies every workbench herdr-app mod into
# HOME/mods/<name>/ (default HERDR_APP_DIR) with a per-file checksum manifest.
# Directories under HERDR_APP_MODS_SRC_DIR whose names start with `_` are shared
# libraries, not mods. Non-fatal: always returns 0.
#
# HERDR_APP_MODS_NEWLY_INSTALLED is a global array this function resets, then
# fills with mod names that received a .workbench-install this run. Callers
# (step_herdr_app_mods) read it to print one-time hints; they must not treat
# it as set until after this function returns.
_herdr_app_install_mods() {
  local home="${1:-$HERDR_APP_DIR}" src_root="$HERDR_APP_MODS_SRC_DIR"
  local mods_home="$home/mods" src_mod name
  HERDR_APP_MODS_NEWLY_INSTALLED=()
  [[ -d "$src_root" ]] || return 0
  mkdir -p "$mods_home" || { warn "Could not create $mods_home"; return 0; }
  for src_mod in "$src_root"/*; do
    [[ -d "$src_mod" ]] || continue
    name=$(basename "$src_mod")
    [[ "$name" == _* ]] && continue
    _herdr_mod_install_one "$src_mod" "$mods_home/$name" "$name"
  done
  return 0
}
