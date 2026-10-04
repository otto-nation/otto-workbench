#!/usr/bin/env bash
# Valid registry fixtures shared by the validate_registries_* suites; each writes into the shared tree setup_file built.

_write_valid_brew() {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  validation: brewfile
  source: brew/Brewfile

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A test tool"
    when_to_use: "When testing"
    usage: "mytool --help"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/Brewfile"
}

_write_valid_bin() {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Workbench Scripts"
  validation: bindir
  source: bin

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
  - name: othertool
    permission: false
    visibility: full
    description: "Another script"
    when_to_use: "When needed"
    usage: "othertool --help"
EOF
}

_write_valid_zsh() {
  cat > "$TMPDIR/zsh/registry.yml" << 'EOF'
meta:
  section: "Shell Aliases"
  validation: zsh-comments
  source: zsh

tools:
  - name: "Git aliases"
    permission: false
    visibility: full
    description: "Git shortcuts"
    when_to_use: "Always"
    usage: "gs"
EOF
  printf '# Git aliases Configuration\nalias gs="git status"\n' \
    > "$TMPDIR/zsh/config.d/aliases-git.zsh"
}

_write_valid_work() {
  cat > "$TMPDIR/brew/work/mystack.registry.yml" << 'EOF'
meta:
  section: "My Stack Tools"
  install_check: true
  validation: brewfile
  source: brew/work/mystack.Brewfile

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A work tool"
    when_to_use: "When working"
    usage: "mytool --help"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/work/mystack.Brewfile"
}
