#!/usr/bin/env bash
# Project scaffolding for `otto-workbench ai init` — sourced by ai/claude/steps.sh,
# never directly. All paths come from lib/constants.sh.

# ─── Project scaffolding ─────────────────────────────────────────────────────
# These functions scaffold a per-project .claude/ directory. Called by
# `otto-workbench ai init` when run from a project root.

# _detect_project_stacks — populates DETECTED_STACKS array with detected languages.
_detect_project_stacks() {
  DETECTED_STACKS=()

  if [[ -f "build.gradle.kts" ]]; then
    DETECTED_STACKS+=("kotlin")
  elif [[ -f "build.gradle" ]] && grep -q "kotlin" "build.gradle" 2>/dev/null; then
    DETECTED_STACKS+=("kotlin")
  elif [[ -f "build.gradle" || -f "pom.xml" ]]; then
    DETECTED_STACKS+=("java")
  fi

  [[ -f "go.mod" ]] && DETECTED_STACKS+=("go")

  if [[ -f "package.json" ]]; then
    if [[ -f "tsconfig.json" ]] || grep -q '"typescript"' "package.json" 2>/dev/null; then
      DETECTED_STACKS+=("typescript")
    else
      DETECTED_STACKS+=("node")
    fi
  fi

  if [[ -f "requirements.txt" || -f "pyproject.toml" || -f "setup.py" ]]; then
    DETECTED_STACKS+=("python")
  fi

  [[ -f "Cargo.toml" ]] && DETECTED_STACKS+=("rust")
  [[ -d "ansible" ]] && DETECTED_STACKS+=("ansible")
  return 0
}

# _detect_build_commands — sets BUILD_CMD, TEST_CMD, RUN_CMD based on primary stack.
_detect_build_commands() {
  BUILD_CMD="" TEST_CMD="" RUN_CMD=""
  local primary="${DETECTED_STACKS[0]:-}"
  case "$primary" in
    kotlin|java)
      if [[ -f "gradlew" ]]; then
        BUILD_CMD="./gradlew build"; TEST_CMD="./gradlew test"
      elif [[ -f "pom.xml" ]]; then
        BUILD_CMD="mvn package"; TEST_CMD="mvn test"
      fi ;;
    go)
      BUILD_CMD="go build ./..."; TEST_CMD="go test ./..."; RUN_CMD="go run ." ;;
    typescript|node)
      local pm="npm"
      [[ -f "bun.lockb" || -f "bun.lock" ]] && pm="bun"
      [[ -f "pnpm-lock.yaml" ]] && pm="pnpm"
      [[ -f "yarn.lock" ]] && pm="yarn"
      BUILD_CMD="${pm} run build"; TEST_CMD="${pm} run test"; RUN_CMD="${pm} run dev" ;;
    python)
      TEST_CMD="pytest"; RUN_CMD="python -m <module>" ;;
    rust)
      BUILD_CMD="cargo build"; TEST_CMD="cargo test"; RUN_CMD="cargo run" ;;
  esac
}

# _build_stack_label — sets STACK_LABEL from DETECTED_STACKS.
_build_stack_label() {
  if [[ ${#DETECTED_STACKS[@]} -eq 0 ]]; then
    STACK_LABEL="(not detected)"; return
  fi
  local labels=() s
  for s in "${DETECTED_STACKS[@]}"; do
    case "$s" in
      kotlin) labels+=("Kotlin") ;; java) labels+=("Java") ;;
      go) labels+=("Go") ;; typescript) labels+=("TypeScript") ;;
      node) labels+=("Node.js") ;; python) labels+=("Python") ;;
      rust) labels+=("Rust") ;; ansible) labels+=("Ansible") ;; *) labels+=("$s") ;;
    esac
  done
  local IFS=", "; STACK_LABEL="${labels[*]}"
}

# _scaffold_file SOURCE TARGET LABEL — copies source to target, skips if exists.
_scaffold_file() {
  local source="$1" target="$2" label="$3" force="${4:-false}"
  if [[ -f "$target" ]] && [[ "$force" == false ]]; then
    skip "$label (exists)"; return
  fi
  cp "$source" "$target"
  success "$label"
}

# _generate_agents_md [FORCE] — writes a lean project AGENTS.md at the root.
#
# AGENTS.md is what both harnesses read (Claude Code natively from
# CLAUDE_CODE_MIN_VERSION, Pi first in its per-directory order). A repo that
# already has an instructions file — AGENTS.md or a legacy CLAUDE.md — is left
# alone without FORCE, and a legacy one gets the rename suggestion: writing a
# fresh AGENTS.md beside it would split the harnesses, Claude Code reading only
# CLAUDE.md and Pi only AGENTS.md. With FORCE the scaffold is rewritten; a
# legacy CLAUDE.md is never deleted, only named.
_generate_agents_md() {
  local force="${1:-false}" target="AGENTS.md" project_name record existing hint
  project_name="$(basename "$(pwd)")"
  record="$(python3 "$PROJECT_CONTEXT_PY" --root .)" || record=""
  existing="${record%%$'\n'*}"
  if [[ -n "$existing" ]] && [[ "$force" == false ]]; then
    skip "$existing (exists)"
    hint="$(python3 "$PROJECT_CONTEXT_PY" --root . --hint 2>/dev/null)" || hint=""
    [[ -z "$hint" ]] || info "$hint"
    return
  fi

  local workflow=""
  [[ -n "$BUILD_CMD" ]] && workflow+="- Build: \`${BUILD_CMD}\`"$'\n'
  [[ -n "$TEST_CMD"  ]] && workflow+="- Test:  \`${TEST_CMD}\`"$'\n'
  [[ -n "$RUN_CMD"   ]] && workflow+="- Run:   \`${RUN_CMD}\`"$'\n'
  workflow="${workflow%$'\n'}"

  cat > "$target" <<EOF
# ${project_name}

## Stack
${STACK_LABEL}

## Dev workflow
${workflow}

## Key paths

## Notes

## Rules
Project conventions load from \`.claude/rules/\` automatically in Claude Code.
Add personal rules as \`.claude/rules/<topic>.local.md\` (gitignored).

This file lives at the repository root as AGENTS.md so every agent harness
reads it — Claude Code and Pi both resolve it there, and neither looks inside
\`.claude/\` for it.
EOF
  # The generated file is the repo's, committed and read by teammates who may
  # not use the workbench, so it names no workbench command or version policy.
  success "AGENTS.md"
  if [[ -n "$existing" && "$existing" != "$target" ]]; then
    warn "$existing is still here — fold anything it holds into AGENTS.md and delete it, or Claude Code keeps reading it instead"
  fi
}

# Everything a project's .claude/ holds that belongs to one machine rather than
# the repo: regenerated context and Claude Code's own per-machine grants. The
# rest of the directory — rules/, settings.json — is committed, so
# these are excluded by name. This repo's own .claude/.gitignore is held to the
# same list by tests/claude_settings_template.bats.
CLAUDE_LOCAL_ARTIFACTS=(anatomy.md ceiling-debt.md settings.local.json)

# _scaffold_gitignore — creates .claude/rules/.gitignore and .claude/.gitignore.
_scaffold_gitignore() {
  local rules_gi=".claude/rules/.gitignore"
  if [[ ! -f "$rules_gi" ]]; then
    printf '*.local.md\n' > "$rules_gi"
  fi

  local review_gi=".claude/review/.gitignore"
  if [[ ! -f "$review_gi" ]]; then
    printf '*.local.md\n' > "$review_gi"
  fi

  # Appended one name at a time rather than rewritten: the file may already
  # carry entries this repo knows nothing about, and a project that predates a
  # new artifact still has to pick it up.
  local claude_gi=".claude/.gitignore" artifact
  touch "$claude_gi"
  for artifact in "${CLAUDE_LOCAL_ARTIFACTS[@]}"; do
    grep -qxF "$artifact" "$claude_gi" || printf '%s\n' "$artifact" >> "$claude_gi"
  done
}

# scaffold_project_claude [--force] — scaffolds .claude/ and the root AGENTS.md
# in the current directory.
# Called by `otto-workbench ai init` for project-level setup.
scaffold_project_claude() {
  local force=false
  [[ "${1:-}" == "--force" ]] && force=true

  _detect_project_stacks
  _detect_build_commands
  _build_stack_label

  if [[ ${#DETECTED_STACKS[@]} -gt 0 ]]; then
    info "Stack: ${STACK_LABEL}"
  else
    warn "No stack detected — generating base scaffold"
  fi
  echo

  info "Scaffolding AGENTS.md"
  _generate_agents_md "$force"

  echo
  mkdir -p .claude/rules .claude/review
  info "Scaffolding .claude/rules/"
  _scaffold_file "$CLAUDE_TEMPLATES_DIR/rules/conventions.md" ".claude/rules/conventions.md" "conventions.md" "$force"
  _scaffold_file "$CLAUDE_TEMPLATES_DIR/rules/testing.md"     ".claude/rules/testing.md"     "testing.md"     "$force"

  local s
  for s in "${DETECTED_STACKS[@]}"; do
    local tmpl="$CLAUDE_TEMPLATES_DIR/rules/${s}.md"
    if [[ -f "$tmpl" ]]; then _scaffold_file "$tmpl" ".claude/rules/${s}.md" "${s}.md" "$force"; fi
  done

  # Scaffold architecture.md for stacks that benefit from architecture narrative
  for s in "${DETECTED_STACKS[@]}"; do
    local arch_tmpl="$CLAUDE_TEMPLATES_DIR/architecture/${s}.md"
    if [[ -f "$arch_tmpl" ]]; then
      echo
      info "Scaffolding .claude/architecture.md"
      _scaffold_file "$arch_tmpl" ".claude/architecture.md" "architecture.md" "$force"
      break
    fi
  done

  _scaffold_gitignore
}
