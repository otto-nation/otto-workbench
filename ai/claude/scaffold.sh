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

# _generate_claude_md TARGET — writes a lean project CLAUDE.md.
_generate_claude_md() {
  local target="$1" force="${2:-false}" project_name
  project_name="$(basename "$(pwd)")"
  if [[ -f "$target" ]] && [[ "$force" == false ]]; then
    skip "CLAUDE.md (exists)"; return
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

This file lives at the repository root so every agent harness reads it — Pi
resolves a context file per directory and never looks inside \`.claude/\`.
EOF
  success "CLAUDE.md"
}

# Everything a project's .claude/ holds that belongs to one machine rather than
# the repo: regenerated context and Claude Code's own per-machine grants. The
# rest of the directory — CLAUDE.md, rules/, settings.json — is committed, so
# these are excluded by name. This repo's own .claude/.gitignore is held to the
# same list by tests/claude_settings.bats.
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

# scaffold_project_claude [--force] — scaffolds .claude/ and the root CLAUDE.md
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

  info "Scaffolding CLAUDE.md"
  _generate_claude_md "CLAUDE.md" "$force"

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
