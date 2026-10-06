#!/usr/bin/env bash
# description: AI component sync — dispatches to installed sub-tools
# AI parent dispatcher — sources all sub-tool steps.sh files and dispatches
# sync to each installed sub-tool.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

_AI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ai_sub_tool_dirs DIR — prints the tool subdirectories of DIR that contain a
# steps.sh, one per line. The single definition of "what counts as an AI
# sub-tool": used here to source every steps.sh, and by ai/setup.sh's
# _ai_discover_tools to build the tool list it presents, so the two glob the
# same rule instead of two copies that could drift apart.
ai_sub_tool_dirs() {
  local base="$1" dir
  for dir in "$base"/*/; do
    if [[ -f "${dir}steps.sh" ]]; then printf '%s\n' "$dir"; fi
  done
}

# Source all sub-tool steps.sh files so sync_<tool> functions are available.
while IFS= read -r _ai_sub; do
  # shellcheck source=/dev/null
  . "${_ai_sub}steps.sh"
done < <(ai_sub_tool_dirs "$_AI_DIR")
unset _ai_sub _AI_DIR

# ai_generate_rules — regenerates git.generated.md and tools.generated*.md.
# Harness-neutral: every harness reads these files, so neither Claude Code's
# install steps nor Pi's guidelines compose may own the regeneration. Called
# from sync_ai and ai/setup.sh after workbench-rules sync, before any tool.
#
# The tool-context and git-rules blocks below are structurally identical
# (executable check -> conditional info -> quiet-gated invocation -> warn on
# missing) apart from the git-rules branch's extra cwd/subshell pinning. Not
# worth a shared helper for two call sites, but if a third generator joins
# this function, factor out a `_run_rule_generator name path output_var
# output_path` helper then.
ai_generate_rules() {
  local tool_gen="$BIN_SRC_DIR/local/generate-tool-context"
  local git_gen="$WORKBENCH_DIR/git/bin/local/generate-git-rules"
  local -a gen_args=()
  [[ "${WORKBENCH_SYNC:-}" == true ]] && gen_args=(--quiet)

  if [[ -x "$tool_gen" ]]; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && info "Generating tool context" || true
    TOOL_CONTEXT_OUTPUT="$WORKBENCH_DIR/$TOOLS_GENERATED_RELPATH" \
      "$tool_gen" "${gen_args[@]}"
  else
    warn "generate-tool-context not found — skipping tool context generation"
  fi

  if [[ -x "$git_gen" ]]; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && info "Generating git rules" || true
    # generate-git-rules derives REPO_ROOT from cwd via git rev-parse, so a
    # sync started outside the workbench tree would write into whatever repo
    # that cwd belongs to. Pin both the cwd and the output path.
    (
      cd "$WORKBENCH_DIR"
      GIT_RULES_OUTPUT="$WORKBENCH_DIR/$GIT_GENERATED_RELPATH" \
        "$git_gen" "${gen_args[@]}"
    )
  else
    warn "generate-git-rules not found — skipping git rules generation"
  fi
}

# ai_scaffold_gh_token_env FILE — writes the commented GH_TOKEN template that
# `pr create` and `pr describe --post` resolve their token from
# (ai/lib/pr/gh_token.py). Creates FILE, owner-only, when absent; appends the
# GH_TOKEN section to an existing FILE that mentions GH_TOKEN nowhere; leaves
# any other FILE byte-identical. Never rewrites a line already present, so it
# is safe to re-run.
#
# An existing file's leftover AI_COMMAND / ANTHROPIC_API_KEY lines are inert
# and deliberately kept: the file is the operator's, and nothing reads them.
ai_scaffold_gh_token_env() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  if [[ ! -f "$file" ]]; then
    (
      umask 077
      {
        printf '# GitHub tokens for pr create and pr describe --post.\n'
        printf '# See ai/guidelines/rules/security-secrets.md for the two-file secret model.\n\n'
        _ai_gh_token_section
        printf '\n'
        _ai_gh_org_section
      } > "$file"
    )
    success "Created ${file}"
    return 0
  fi
  if grep -q 'GH_TOKEN' "$file"; then
    return 0
  fi
  { printf '\n'; _ai_gh_token_section; } >> "$file"
  success "Added GH_TOKEN section to ${file}"
}

_ai_gh_token_section() {
  cat <<'EOF'
# ── GitHub token (used by: pr create, pr describe --post) ──────────────────────
# Create a fine-grained PAT at https://github.com/settings/tokens/new
# Permissions: Contents (read/write), Pull requests (read/write)
# Scope to specific repos only — never "All repositories"
# GH_TOKEN=github_pat_
EOF
}

_ai_gh_org_section() {
  cat <<'EOF'
# ── Per-org tokens (optional — overrides GH_TOKEN for repos in that org) ──────
# Use GH_TOKEN__<ORG> with the org name uppercased and hyphens as underscores.
# Each PAT should be scoped to that org's repos only.
# GH_TOKEN__OTTO_NATION=github_pat_
# GH_TOKEN__MY_WORK_ORG=github_pat_
EOF
}

# sync_ai — dispatches to each installed AI sub-tool's sync function.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_ai() {
  local _tool
  local -a _tools=()

  # ai/bin holds the CLIs that serve every harness — pr, otto-log, ceiling-scan
  # and the rest. They install ahead of the selection check and regardless of
  # what it holds: a machine running Pi alone still needs them on PATH, and a
  # machine with no sub-tool selected at all still has them in its registry.
  sync_component_bin "$AI_SRC_DIR"

  # Refresh this machine's own rule layers before any harness installs from
  # them. No harness owns them, so this cannot live in one: a machine running
  # Pi alone used to get its rules only because Claude Code's sync had written
  # them, and so on a machine without it got none at all.
  "$AI_SRC_DIR/bin/workbench-rules" sync

  # Same window, same reason: the generated git and tool-context files are
  # inputs to every harness, not outputs of one.
  ai_generate_rules

  while IFS= read -r _tool; do
    [[ -z "$_tool" ]] && continue
    _tools+=("$_tool")
  done < <(state_get_list "ai.tools")

  # Guards the expansion below, which is an unbound-variable error on an empty
  # array in the bash the framework targets.
  if [[ ${#_tools[@]} -eq 0 ]]; then
    return 0
  fi

  for _tool in "${_tools[@]}"; do
    if declare -f "sync_${_tool}" > /dev/null; then
      "sync_${_tool}"
    fi
  done
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}AI sync${NC}\n"
  sync_ai
  echo
  success "AI sync complete!"
fi
