#!/usr/bin/env bash
# Project AI scaffold — `otto-workbench ai init`. Streams Claude tool-use
# progress while `/analyze-project` runs, then writes `.claude/` in the
# current git worktree.
#
# Sourced by `bin/otto-workbench`. `cmd_ai_init` is a one-line wrapper.

[[ -n "${_LIB_AI_INIT_SH:-}" ]] && return
_LIB_AI_INIT_SH=1

# Guard: constants must be loaded
if [[ -z "${WORKBENCH_DIR:-}" ]]; then
  echo "ERROR: lib/ai_init.sh requires WORKBENCH_DIR (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

# _stream_progress SESSION_LOG — reads stream-json from stdin, shows tool-use progress, saves raw log.
_stream_progress() {
  local session_log="$1"
  : > "$session_log"
  local prev_label=""
  while IFS= read -r line; do
    echo "$line" >> "$session_log"
    local msg_type
    msg_type=$(echo "$line" | jq -r '.type // empty' 2>/dev/null) || continue
    [[ "$msg_type" == "assistant" ]] || continue

    local labels
    labels=$(echo "$line" | jq -r '
      .message.content[]? | select(.type == "tool_use") |
      if .input.description then
        .input.description
      elif .name == "Read" then
        "Read " + (.input.file_path | split("/") | last)
      elif .name == "Grep" then
        "Grep " + (.input.pattern // "")
      elif .name == "Glob" then
        "Glob " + (.input.pattern // "")
      elif .name == "Write" then
        "Write " + (.input.file_path | split("/") | last)
      else
        .name
      end
    ' 2>/dev/null)
    [[ -z "$labels" ]] && continue

    while IFS= read -r label; do
      [[ -z "$label" ]] && continue
      [[ "$label" == "$prev_label" ]] && continue
      echo -e "  ${DIM}▸ ${label}${NC}"
      prev_label="$label"
    done <<< "$labels"
  done
}

# ai_init_run [ --force | --analyze | -h | --help ] — scaffold `.claude/`
# in the current git worktree. `--force` re-scaffolds; `--analyze` runs
# /analyze-project after. Returns rather than exiting so the CLI wrapper
# can keep the process.
ai_init_run() {
  local force_flag="" analyze=false

  local arg
  for arg in "$@"; do
    case "$arg" in
      --force)    force_flag="--force" ;;
      --analyze)  analyze=true ;;
      -h|--help)
        cat <<EOF
Usage: otto-workbench ai init [--force] [--analyze]

Scaffold a .claude/ directory in the current git repo with stack-detected
rules, conventions, and a project anatomy file.

Options:
  --force     Re-scaffold an existing .claude/ directory
  --analyze   Run /analyze-project after scaffolding to populate files
EOF
        return 0
        ;;
    esac
  done

  title "AI init"
  echo

  # The scaffold is a tree of tracked files, so it goes in a working tree. A
  # shell sitting in a bare-repo container has none of its own, and project_root
  # names the worktree the container stands in for.
  #
  # Both unresolved cases warn and return rather than failing: this step is one
  # part of a longer init, and "no tree here to scaffold" has always been a skip.
  # A single-purpose writer like serena-mcp has nothing left to do and exits 1.
  local root rc=0
  root="$(project_root)" || rc=$?
  if [[ "$rc" -eq 2 ]]; then
    echo
    warn "Not in a git repo — skipping project scaffold and anatomy"
    return
  fi
  if [[ "$rc" -ne 0 ]]; then
    echo
    warn "No worktree resolved for $PWD — skipping project scaffold and anatomy"
    return
  fi

  cd "$root" || return

  # Scaffolding a repo is as clear a statement that it uses the workbench as
  # there is, and the root is already resolved here.
  project_register "$root" || true

  local did_scaffold=false
  if [[ -d ".claude" ]] && [[ -z "$force_flag" ]]; then
    info "Project .claude/ exists — skipping scaffold (use --force to re-scaffold)"
  else
    info "Scaffolding project"
    scaffold_project_claude $force_flag
    did_scaffold=true
  fi

  local anatomy_gen="$CLAUDE_SKILLS_DIR/anatomy/generate-anatomy.sh"
  if [[ -f "$anatomy_gen" ]]; then
    echo
    info "Generating project anatomy"
    if bash "$anatomy_gen"; then
      success "anatomy.md"
    else
      skip "anatomy.md (generator failed)"
    fi
  fi

  local skill_file="$CLAUDE_SKILLS_DIR/analyze-project/SKILL.md"
  if [[ "$did_scaffold" == true ]] && command -v claude >/dev/null 2>&1 && [[ -f "$skill_file" ]]; then
    echo
    local run_analyze=false
    if [[ "$analyze" == true ]] || confirm "Run /analyze-project now to populate the scaffolded files?"; then
      run_analyze=true
    fi

    if [[ "$run_analyze" == true ]]; then
      local session_log=".claude/analyze-project.session.jsonl"
      info "Running /analyze-project"
      echo
      local skill_content
      skill_content=$(awk 'NR==1 && /^---$/{f=1;next} f && /^---$/{f=0;next} !f{print}' "$skill_file")
      claude -p --verbose --output-format stream-json \
        --permission-mode acceptEdits \
        "$skill_content" \
        | _stream_progress "$session_log"
      echo
    fi
  fi

  echo
  success "AI init complete!"
}
