# Git Configuration

# ============================================================================
# Short Forms
# ============================================================================

# Short forms (complex operations live in ~/.gitconfig)
alias gs='git status'
alias ga='git add'
alias gc='git commit'
alias gp='git push'
alias gl='git log --oneline --graph --decorate'
alias gco='git checkout'
alias gb='git branch'
alias gd='git diff'

# ============================================================================
# Functions
# ============================================================================

# Clean up merged git branches (except main/master/development)
git-clean-branches() {
  git branch --merged | grep -vE '^\*|main|master|development' | xargs -n 1 git branch -d
}
