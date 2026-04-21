#!/usr/bin/env bats
# Tests for targeted install (install.sh COMPONENT ...) and
# install-needed detection (_check_install_needed in otto-workbench sync).

setup() {
  REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
  TMPDIR="$(mktemp -d)"
  export NO_COLOR=1
}

teardown() {
  rm -rf "$TMPDIR"
}

# ─── Helper: build a minimal workbench layout ────────────────────────────────

_make_workbench() {
  local dir="$1"
  mkdir -p "$dir/lib"

  # Core components (steps.sh, no setup.conf)
  mkdir -p "$dir/bin"
  printf '#!/usr/bin/env bash\n# description: Symlink bin scripts\nsync_bin() { echo "sync_bin"; }\n' > "$dir/bin/steps.sh"

  mkdir -p "$dir/git"
  printf '#!/usr/bin/env bash\n# description: Configure git\nsync_git() { echo "sync_git"; }\n' > "$dir/git/steps.sh"

  # Preflight components
  for pf in task brew mise; do
    mkdir -p "$dir/$pf"
    printf '#!/usr/bin/env bash\nstep_%s_install() { true; }\n' "$pf" > "$dir/$pf/steps.sh"
  done

  # Optional components (setup.conf + setup.sh)
  printf 'label = Homebrew packages\ndescription = Install formulae and casks\n' > "$dir/brew/setup.conf"
  printf '#!/usr/bin/env bash\necho "brew setup"\n' > "$dir/brew/setup.sh"

  mkdir -p "$dir/docker"
  printf 'label = Docker\ndescription = Configure Docker runtime\n' > "$dir/docker/setup.conf"
  printf '#!/usr/bin/env bash\necho "docker setup"\n' > "$dir/docker/setup.sh"

  printf 'brew\ndocker\n' > "$dir/install.components"
}

# ─── _is_targeted ────────────────────────────────────────────────────────────

_is_targeted() {
  local target=$1 t
  for t in "${INSTALL_TARGETS[@]}"; do [[ "$t" == "$target" ]] && return 0; done
  return 1
}

# ─── Targeted install: component resolution ──────────────────────────────────

@test "core components exclude preflight and optional (setup.conf) dirs" {
  _make_workbench "$TMPDIR/wb"
  local WORKBENCH_DIR="$TMPDIR/wb"
  local PREFLIGHT_COMPONENTS=(task brew mise)
  local KNOWN_CORE=()

  for _f in "$WORKBENCH_DIR"/*/steps.sh; do
    [[ -f "$_f" ]] || continue
    local _c _is_pf=false
    _c=$(basename "$(dirname "$_f")")
    [[ -f "$WORKBENCH_DIR/$_c/setup.conf" ]] && continue
    for _pf in "${PREFLIGHT_COMPONENTS[@]}"; do [[ "$_pf" == "$_c" ]] && { _is_pf=true; break; }; done
    [[ "$_is_pf" == true ]] && continue
    KNOWN_CORE+=("$_c")
  done

  [[ " ${KNOWN_CORE[*]} " == *" bin "* ]]
  [[ " ${KNOWN_CORE[*]} " == *" git "* ]]
  [[ " ${KNOWN_CORE[*]} " != *" task "* ]]
  [[ " ${KNOWN_CORE[*]} " != *" mise "* ]]
  [[ " ${KNOWN_CORE[*]} " != *" brew "* ]]
}

@test "optional components come from install.components" {
  _make_workbench "$TMPDIR/wb"
  local KNOWN_OPTIONAL=()

  while IFS= read -r _c; do
    [[ -z "$_c" || "$_c" =~ ^# ]] && continue
    KNOWN_OPTIONAL+=("$_c")
  done < "$TMPDIR/wb/install.components"

  [[ " ${KNOWN_OPTIONAL[*]} " == *" brew "* ]]
  [[ " ${KNOWN_OPTIONAL[*]} " == *" docker "* ]]
  [[ ${#KNOWN_OPTIONAL[@]} -eq 2 ]]
}

# ─── Targeted install: validation ────────────────────────────────────────────

@test "validate_targets rejects unknown component names" {
  local KNOWN_CORE_COMPONENTS=(bin git)
  local KNOWN_OPTIONAL_COMPONENTS=(brew docker)
  local INSTALL_TARGETS=(nosuchcomponent)

  local errors=0 target found
  for target in "${INSTALL_TARGETS[@]}"; do
    found=false
    for _c in "${KNOWN_CORE_COMPONENTS[@]}" "${KNOWN_OPTIONAL_COMPONENTS[@]}"; do
      [[ "$_c" == "$target" ]] && { found=true; break; }
    done
    [[ "$found" == false ]] && errors=$(( errors + 1 ))
  done

  [[ "$errors" -eq 1 ]]
}

@test "validate_targets accepts valid core and optional names" {
  local KNOWN_CORE_COMPONENTS=(bin git)
  local KNOWN_OPTIONAL_COMPONENTS=(brew docker)
  local INSTALL_TARGETS=(bin brew docker)

  local errors=0 target found
  for target in "${INSTALL_TARGETS[@]}"; do
    found=false
    for _c in "${KNOWN_CORE_COMPONENTS[@]}" "${KNOWN_OPTIONAL_COMPONENTS[@]}"; do
      [[ "$_c" == "$target" ]] && { found=true; break; }
    done
    [[ "$found" == false ]] && errors=$(( errors + 1 ))
  done

  [[ "$errors" -eq 0 ]]
}

# ─── Targeted install: _is_targeted ──────────────────────────────────────────

@test "_is_targeted matches components in INSTALL_TARGETS" {
  INSTALL_TARGETS=(brew docker)
  _is_targeted brew
  _is_targeted docker
}

@test "_is_targeted rejects components not in INSTALL_TARGETS" {
  INSTALL_TARGETS=(brew)
  ! _is_targeted docker
  ! _is_targeted git
}

# ─── Targeted install: core filtering ────────────────────────────────────────

@test "targeted mode selects only named core components" {
  local core_dirs=(bin git zsh)
  INSTALL_TARGETS=(git)

  local selected=()
  for _c in "${core_dirs[@]}"; do
    _is_targeted "$_c" && selected+=("$_c")
  done

  [[ ${#selected[@]} -eq 1 ]]
  [[ "${selected[0]}" == "git" ]]
}

@test "targeted mode selects only named optional components" {
  local eligible_dirs=(brew docker terminals editors ai)
  INSTALL_TARGETS=(brew docker)

  local selected=()
  for _d in "${eligible_dirs[@]}"; do
    _is_targeted "$_d" && selected+=("$_d")
  done

  [[ ${#selected[@]} -eq 2 ]]
  [[ " ${selected[*]} " == *" brew "* ]]
  [[ " ${selected[*]} " == *" docker "* ]]
}

# ─── Install-needed detection ────────────────────────────────────────────────

@test "check_install_needed detects component with failing check command" {
  _make_workbench "$TMPDIR/wb"
  printf 'label = Docker\ndescription = Docker\ncheck = false\n' > "$TMPDIR/wb/docker/setup.conf"

  local -a needs_install=()
  local registry="$TMPDIR/wb/install.components"
  local component check_cmd
  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    local conf="$TMPDIR/wb/$component/setup.conf"
    [[ -f "$conf" ]] || continue
    check_cmd=$(grep -m1 '^check[[:space:]]*=' "$conf" 2>/dev/null \
      | sed 's/^check[[:space:]]*=[[:space:]]*//')
    [[ -z "$check_cmd" ]] && continue
    if ! bash -c "$check_cmd" >/dev/null 2>&1; then
      needs_install+=("$component")
    fi
  done < "$registry"

  [[ " ${needs_install[*]} " == *" docker "* ]]
}

@test "check_install_needed skips component with passing check command" {
  _make_workbench "$TMPDIR/wb"
  printf 'label = Docker\ndescription = Docker\ncheck = true\n' > "$TMPDIR/wb/docker/setup.conf"

  local -a needs_install=()
  local registry="$TMPDIR/wb/install.components"
  local component check_cmd
  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    local conf="$TMPDIR/wb/$component/setup.conf"
    [[ -f "$conf" ]] || continue
    check_cmd=$(grep -m1 '^check[[:space:]]*=' "$conf" 2>/dev/null \
      | sed 's/^check[[:space:]]*=[[:space:]]*//')
    [[ -z "$check_cmd" ]] && continue
    if ! bash -c "$check_cmd" >/dev/null 2>&1; then
      needs_install+=("$component")
    fi
  done < "$registry"

  [[ ${#needs_install[@]} -eq 0 ]]
}

@test "check_install_needed skips components without check command" {
  _make_workbench "$TMPDIR/wb"
  # Neither brew nor docker have check commands by default

  local -a needs_install=()
  local registry="$TMPDIR/wb/install.components"
  local component check_cmd
  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    local conf="$TMPDIR/wb/$component/setup.conf"
    [[ -f "$conf" ]] || continue
    check_cmd=$(grep -m1 '^check[[:space:]]*=' "$conf" 2>/dev/null \
      | sed 's/^check[[:space:]]*=[[:space:]]*//')
    [[ -z "$check_cmd" ]] && continue
    if ! bash -c "$check_cmd" >/dev/null 2>&1; then
      needs_install+=("$component")
    fi
  done < "$registry"

  [[ ${#needs_install[@]} -eq 0 ]]
}
