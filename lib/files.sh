#!/usr/bin/env bash
# File operations with idempotency: symlinks, copies, directory operations,
# layer merging, and the frontmatter reads that decide where a layered item installs.
#
# Bash-only — it uses `local`, arrays, and the prompt helpers.

[[ -n "${_LIB_FILES_SH:-}" ]] && return
_LIB_FILES_SH=1

# Ensure dependencies are available
_files_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=output.sh
. "$_files_lib_dir/output.sh"
# shellcheck source=prompts.sh
. "$_files_lib_dir/prompts.sh"
unset _files_lib_dir

# install_symlink SOURCE TARGET [LABEL] [--no-prompt]
# Creates or updates a symlink at TARGET pointing to SOURCE. LABEL defaults to
# the basename of SOURCE.
#
# Existing symlinks are silently replaced. Real files at TARGET:
#   default (or SYMLINK_MODE unset): prompt before overwriting
#   --no-prompt or SYMLINK_MODE=no-prompt: warn and skip (for non-interactive sync)
#
# -h prevents BSD ln from dereferencing an existing directory symlink on re-runs.
install_symlink() {
  local source=$1 target=$2
  shift 2

  # In bare repos, redirect symlink targets to the stable (main) worktree
  # so symlinks survive worktree switches.
  if [[ "${WORKBENCH_STABLE_DIR:-}" != "${WORKBENCH_DIR:-}" ]]; then
    source="${source/#"$WORKBENCH_DIR"/"$WORKBENCH_STABLE_DIR"}"
  fi
  local label="" no_prompt=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-prompt) no_prompt=true; shift ;;
      *)           label="$1";     shift ;;
    esac
  done

  [[ -z "$label" ]] && label=$(basename "$source")

  if [[ -L "$target" ]] && [[ "$(readlink "$target")" == "$source" ]]; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}✓ $label${NC}" || true
    return
  fi

  if [[ -e "$target" && ! -L "$target" ]]; then
    if [[ "$no_prompt" == true || "${SYMLINK_MODE:-}" == "no-prompt" ]]; then
      warn "$label: real file exists at $target — skipping (run install.sh to manage)"
      return
    fi
    prompt_overwrite "$target" || { skip "$label"; return; }
  fi

  # -sfh (BSD/macOS) and -sfn (GNU/Linux) both prevent following an existing symlink
  # at the destination — without this, ln -sf on a dir symlink nests inside it.
  if ln --version &>/dev/null 2>&1; then
    ln -sfn "$source" "$target"   # GNU ln
  else
    ln -sfh "$source" "$target"   # BSD ln (macOS)
  fi
  echo -e "  ${GREEN}✓${NC} $label"
}

# install_file SOURCE TARGET [LABEL]
# Copies SOURCE to TARGET if content differs. Removes stale symlinks at TARGET.
# Idempotent — no-op if file is already up to date.
install_file() {
  local source=$1 target=$2
  shift 2
  local label="${1:-$(basename "$source")}"

  if [[ -L "$target" ]]; then
    rm "$target"
  fi

  if [[ -f "$target" ]] && diff -q "$source" "$target" &>/dev/null; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}✓ $label${NC}" || true
    return
  fi

  cp "$source" "$target"
  echo -e "  ${GREEN}✓${NC} $label"
}

# copy_dir SRC DST [GLOB] [--strip-ext] [--prune]
# Copies all files matching GLOB in SRC into DST, preserving filenames.
# GLOB defaults to '*'. --strip-ext removes the file extension from the display label.
# --prune removes stale files (or symlinks) in DST whose source counterpart is gone.
copy_dir() {
  local src="${1%/}" dst="$2"
  shift 2
  local glob="*" strip_ext=false prune=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --strip-ext) strip_ext=true; shift ;;
      --prune)     prune=true;     shift ;;
      *)           glob="$1";      shift ;;
    esac
  done

  if [[ "$prune" == true ]]; then
    local item
    for item in "$dst"/$glob; do
      [[ -e "$item" || -L "$item" ]] || continue
      [[ ! -e "$src/$(basename "$item")" ]] || continue
      rm "$item"
      [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}⊘ pruned $(basename "$item")${NC}" || true
    done
  fi

  local item label
  for item in "$src"/$glob; do
    [[ -f "$item" ]] || continue
    label=$(basename "$item")
    [[ "$strip_ext" == true ]] && label="${label%.*}"
    install_file "$item" "$dst/$(basename "$item")" "$label"
  done
}

# symlink_dir SRC DST [GLOB] [--strip-ext] [--prune] [--replace-copies]
# Symlinks all items matching GLOB in SRC into DST, preserving filenames. GLOB
# defaults to '*'.
#
# --strip-ext removes the file extension from the display label.
# --prune removes stale symlinks in DST that point into SRC but whose source is gone.
# --replace-copies removes regular files in DST that have a source counterpart,
#   allowing install_symlink to replace them. Used when migrating from copy_dir.
#
# Inherits SYMLINK_MODE from the environment (pass-through to install_symlink).
symlink_dir() {
  local src="${1%/}" dst="$2"  # strip trailing slash so item paths never contain //
  shift 2
  local glob="*" strip_ext=false prune=false replace_copies=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --strip-ext)       strip_ext=true;      shift ;;
      --prune)           prune=true;           shift ;;
      --replace-copies)  replace_copies=true;  shift ;;
      *)                 glob="$1";            shift ;;
    esac
  done

  # Replace regular files with source counterparts so install_symlink can create symlinks.
  # install_symlink skips (no-prompt) or prompts (interactive) when a real file exists.
  if [[ "$replace_copies" == true ]]; then
    local item
    for item in "$dst"/$glob; do
      [[ -f "$item" && ! -L "$item" ]] || continue
      [[ -e "$src/$(basename "$item")" ]] || continue
      rm "$item"
    done
  fi

  if [[ "$prune" == true ]]; then
    # Stable dir: match symlinks pointing to any worktree's version of this dir
    local stable_src="${src/#"$WORKBENCH_DIR"/"${WORKBENCH_STABLE_DIR:-$WORKBENCH_DIR}"}"
    local item target
    for item in "$dst"/$glob; do
      [[ -L "$item" ]] || continue
      target=$(readlink "$item")
      [[ "$target" == "$src"/* || "$target" == "$stable_src"/* ]] || continue
      [[ -e "$target" ]] && continue
      rm "$item"
      [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}⊘ pruned $(basename "$item")${NC}" || true
    done
  fi

  local item label
  for item in "$src"/$glob; do
    [[ -e "$item" ]] || continue
    [[ -d "$item" ]] && continue  # never symlink subdirectories (e.g. bin/local/)
    label=$(basename "$item")
    [[ "$strip_ext" == true ]] && label="${label%.*}"
    install_symlink "$item" "$dst/$(basename "$item")" "$label"
  done
}

# sync_component_bin COMPONENT_DIR — symlinks extensionless scripts from
# COMPONENT_DIR/bin/ into LOCAL_BIN_DIR. No-op if bin/ subdirectory is absent.
sync_component_bin() {
  local component_bin="$1/bin"
  [[ -d "$component_bin" ]] || return 0
  mkdir -p "$LOCAL_BIN_DIR"
  shopt -s extglob
  symlink_dir "$component_bin" "$LOCAL_BIN_DIR" "!(*.*)" --prune
  shopt -u extglob
}

# list_shell_scripts ROOT — prints every file under ROOT whose *first* line is a
# shell or bats shebang, one per line, sorted. Skips .git, ignore/, __pycache__,
# node_modules/, and .py.
#
# The awk pass is what anchors to line 1: `grep -r` is line-based, so a shebang
# inside a heredoc (as in a bats fixture) would otherwise select the file.
#
# bats suites are included deliberately — ShellCheck parses them natively, and
# its bats-specific checks catch assertions that silently never fail.
list_shell_scripts() {
  local root="$1"
  local shebang_re='^#!.*(/(ba)?sh|/env (ba)?sh|/bats|/env bats)'
  grep -rlE "$shebang_re" "$root" \
    --exclude-dir='.git' --exclude-dir='ignore' --exclude-dir='__pycache__' \
    --exclude-dir='node_modules' \
    --exclude='*.py' \
    | sort \
    | xargs awk -v re="$shebang_re" 'FNR == 1 && $0 ~ re { print FILENAME }'
}

# resolve_layers BASE_DIR USER_DIR GLOB RESULT_NAMEREF
# Merges two directory layers into an associative array: basename -> source_path.
# User dir wins for same-named files. A .disabled sentinel in user dir suppresses both.
# RESULT_NAMEREF must be a declared associative array in the caller.
resolve_layers() {
  local base_dir="${1%/}" user_dir="${2%/}" glob="$3"
  local -n __result=$4

  # Base layer — all matching items
  local item name
  for item in "$base_dir"/$glob; do
    [[ -e "$item" ]] || continue
    item="${item%/}"  # strip trailing slash from directory globs
    name=$(basename "$item")
    __result["$name"]="$item"
  done

  # User layer — overrides and additions
  if [[ -d "$user_dir" ]]; then
    for item in "$user_dir"/$glob; do
      [[ -e "$item" ]] || continue
      item="${item%/}"
      name=$(basename "$item")
      __result["$name"]="$item"
    done

    # Disable layer — .disabled sentinels suppress both layers
    for item in "$user_dir"/*.disabled; do
      [[ -e "$item" ]] || continue
      name=$(basename "$item" .disabled)
      unset "__result[$name]"
      # Try common extensions
      unset "__result[${name}.md]"
    done
  fi
}

# resolve_rules RESULT_NAMEREF
# Merges the three rule layers into an associative array: basename -> source path.
# Later layers win for a same-named file: the repo's defaults, then what
# workbench-rules generated for this machine, then the operator's override layer.
# A .disabled sentinel in the override layer suppresses a rule from any of them.
# RESULT_NAMEREF must be a declared associative array in the caller.
#
# The one answer to "which rules does this machine apply", because each harness
# would otherwise have to reconstruct it: before this existed, Pi read Claude
# Code's installed rules directory to get the merged set, which is why a machine
# without Claude Code ended up with no Pi context file at all. Each harness now
# takes this set and filters it by its own scoping rules — neither reads the
# other's output.
#
# Two passes so the override layer is last and therefore final. It is the only
# layer an operator writes by hand, so a file they put there has to beat both the
# shipped default and the generated one, and their sentinels have to be able to
# suppress either. The second pass names the generated layer as its base and so
# re-adds entries the first already holds, which is the price of keeping the
# sentinel handling in resolve_layers rather than repeating it here.
resolve_rules() {
  resolve_layers "$GUIDELINES_RULES_SRC_DIR" "$GENERATED_RULES_DIR" "$RULES_GLOB" "$1"
  resolve_layers "$GENERATED_RULES_DIR" "$USER_RULES_DIR" "$RULES_GLOB" "$1"
}

# rules_layer_roots — prints the directory each rule layer is resolved from, one
# per line, so a caller pruning stale installs can tell a link it owns from one
# an operator made by hand.
rules_layer_roots() {
  printf '%s\n' "$GUIDELINES_RULES_SRC_DIR" "$USER_RULES_DIR" "$GENERATED_RULES_DIR"
}

# is_disabled USER_DIR NAME — returns 0 if a .disabled sentinel exists.
is_disabled() {
  [[ -f "${1%/}/${2}.disabled" ]]
}

# frontmatter_field FILE KEY — prints the value KEY carries in FILE's opening
# YAML frontmatter block, and nothing at all when the key is absent, the file
# has no frontmatter, or the path does not exist. A block sequence prints one
# entry per line; an inline value prints as one line.
#
# Both YAML list forms are read, because the two this repo uses are written
# differently: `harness: [claude]` inline, and the `paths:` of every scoped rule
# as an indented `- ` sequence on the lines below. A reader that only takes the
# same-line value answers empty for the second, and every caller asking "is this
# rule path-scoped?" then silently answers no.
#
# Only a matched surrounding quote pair is stripped, never quotes inside the
# value — several skill descriptions carry an apostrophe, and deleting it
# corrupts the prose this was only meant to unwrap. Brackets are left in place
# for the same reason in reverse: `harness: []` must stay distinguishable from a
# harness key that is not there at all, and stripping them makes both empty.
#
# Anchored on a block opening at line 1, because a `---` further down is a
# horizontal rule: reading a key out of a file's prose would act on the
# strength of its own documentation. Answering empty for a missing file keeps a
# caller that meets a half-written directory on its skip path rather than on
# awk's exit 2.
frontmatter_field() {
  [[ -f "$1" ]] || return 0
  awk -v key="$2" '
    function unquote(s) {
      sub(/^[[:space:]]+/, "", s)
      sub(/[[:space:]]+$/, "", s)
      if (s ~ /^".*"$/ || s ~ /^\047.*\047$/) s = substr(s, 2, length(s) - 2)
      return s
    }
    NR == 1 && /^---$/ { in_fm = 1; next }
    in_fm && /^---$/ { exit }
    in_fm && collecting {
      if ($0 ~ /^[[:space:]]*-[[:space:]]*/) {
        sub(/^[[:space:]]*-[[:space:]]*/, "")
        print unquote($0)
        next
      }
      exit
    }
    in_fm && index($0, key ":") == 1 {
      sub(/^[^:]*:[[:space:]]*/, "")
      if ($0 == "") { collecting = 1; next }
      print unquote($0)
      exit
    }' "$1"
}

# rule_harness_ok FILE HARNESS — true when FILE's `harness:` frontmatter list is
# absent, or is present and names HARNESS. Either YAML list form is accepted,
# and whitespace, brackets and quotes around the entries are ignored; a list
# naming nothing at all is false for every harness.
#
# An absent key means every harness, which is all but one rule today — declaring
# the common case would put the burden on the many to spare the one. A list that
# resolves to no harness is a typo the caller should not silently honour, so it
# answers false here and bin/local/validate-rules fails on it outright.
#
# Path scoping is deliberately not part of this answer. A `paths:` rule is
# conditionally loaded under Claude Code and cannot be loaded at all under Pi,
# so what to do about it is the caller's question, not this one's.
rule_harness_ok() {
  local harnesses
  harnesses="$(frontmatter_field "$1" harness)"
  [[ -z "$harnesses" ]] && return 0
  harnesses="${harnesses//$'\n'/,}"
  harnesses="${harnesses//[[:space:]]/}"
  harnesses="${harnesses//\[/}"
  harnesses="${harnesses//\]/}"
  harnesses="${harnesses//\"/}"
  harnesses="${harnesses//\'/}"
  [[ ",${harnesses}," == *",$2,"* ]]
}

# skill_agent SKILL_FILE — prints the agent name SKILL_FILE's frontmatter declares,
# and nothing at all for a skill that declares none or for a path that does not
# exist yet.
#
# The single answer to "is this skill agent-backed?", which is a question three
# subsystems ask and would otherwise each answer their own way: ai/skills/steps.sh
# splices the agent's protocol into the Pi copy and installs nothing Claude-side,
# ai/claude/steps.sh keeps such a skill out of the config export, and
# bin/local/generate-tool-context omits it from both Claude-facing doc blocks. The
# three disagreeing is a skill that installs one way and is documented another.
#
# Anchored on a frontmatter block opening at line 1, because a `---` further down
# is a horizontal rule: reading an `agent:` out of a skill's prose would route it
# away from Claude Code on the strength of its own documentation. Answering empty
# for a missing file keeps a caller that meets a half-written skill directory on
# the skip path rather than on awk's exit 2.
skill_agent() {
  frontmatter_field "$1" agent
}

# install_hook_dispatcher SOURCE_RELPATH TARGET [LABEL]
# Writes a thin dispatcher script that execs the hook from the current worktree.
# Unlike symlinks, dispatchers resolve at runtime — so worktrees always run
# their own branch's version of the hook, not main's.
install_hook_dispatcher() {
  local source_rel="$1" target="$2"
  local label="${3:-$(basename "$target")}"

  # Remove broken symlinks left over from pre-bare-repo installs
  [[ -L "$target" ]] && rm -f "$target"

  local expected
  expected="$(printf '#!/usr/bin/env bash\nexec "$(git rev-parse --show-toplevel)/%s" "$@"\n' "$source_rel")"

  if [[ -f "$target" ]] && [[ "$(cat "$target")" == "$expected" ]]; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}✓ $label ${DIM}(dispatcher → $source_rel)${NC}" || true
    return
  fi

  printf '%s\n' "$expected" > "$target"
  chmod +x "$target"
  echo -e "  ${GREEN}✓${NC} $label ${DIM}(dispatcher → $source_rel)${NC}"
}

# apply_config_patch FILE OLD NEW
# Replaces OLD with NEW in FILE if OLD is present. Idempotent — no-op if already patched
# or if FILE does not exist. Assumes OLD and NEW do not contain the | character.
#
# Called by the migrations under <component>/migrations/, which
# run_component_migrations discovers and runs.
apply_config_patch() {
  local file="$1" old="$2" new="$3"
  [[ -f "$file" ]] || return 0
  grep -qF "$old" "$file" || return 0
  sed_i "s|$old|$new|g" "$file"
  success "Patched $(basename "$file"): '$old' → '$new'"
}
