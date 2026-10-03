# Shared helper for the generate-tool-context test files.
# Loaded in setup() after test_helper, so common_setup has pinned $TMPDIR.

# Source the generator and point every input and output at temp paths, so a
# test never touches real workbench files (registry data, tools.generated.md).
gtc_setup() {
  source "$REPO_ROOT/bin/local/generate-tool-context"
  ORIG_DIR="$PWD"

  mkdir -p "$TMPDIR/brew" "$TMPDIR/bin" "$TMPDIR/zsh" "$TMPDIR/mise"
  export BREW_REGISTRY="$TMPDIR/brew/registry.yml"
  export MISE_REGISTRY="$TMPDIR/mise/registry.yml"
  export BIN_REGISTRY="$TMPDIR/bin/registry.yml"
  export ZSH_REGISTRY="$TMPDIR/zsh/registry.yml"
  export BREW_STACKS_DIR="$TMPDIR"
  export WORK_DIR="$TMPDIR/work"
  export TOOL_CONTEXT_OUTPUT="$TMPDIR/tools.generated.md"
  export TASKFILE_PATH="$TMPDIR/Taskfile.yml"
  export AI_DIR="$TMPDIR/ai"
  export REGISTRY_SCAN_DIR="$TMPDIR"

  mkdir -p "$WORK_DIR"
}

gtc_teardown() {
  cd "$ORIG_DIR" || return 1
  unset BREW_REGISTRY MISE_REGISTRY BIN_REGISTRY ZSH_REGISTRY BREW_STACKS_DIR WORK_DIR TOOL_CONTEXT_OUTPUT REGISTRY_SCAN_DIR AI_DIR TASKFILE_PATH
}
