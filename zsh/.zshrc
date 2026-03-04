# ============================================================================
# ZSH Configuration — workbench template
# ============================================================================
# Copy to ~/.zshrc on first install (managed by install.sh).
# Machine-specific settings and secrets go in ~/.env.local — sourced
# automatically below, never committed.
#
# Prerequisites:
#   - oh-my-zsh:  https://ohmyz.sh/
#   - Homebrew:   https://brew.sh/
#   - SDKMAN:     https://sdkman.io/
# ============================================================================

# ============================================================================
# KIRO CLI — pre block (no-op if not installed)
# ============================================================================
[[ -f "${HOME}/Library/Application Support/kiro-cli/shell/zshrc.pre.zsh" ]] && \
  builtin source "${HOME}/Library/Application Support/kiro-cli/shell/zshrc.pre.zsh"

# ============================================================================
# OH-MY-ZSH
# ============================================================================
export ZSH="$HOME/.oh-my-zsh"

# Theme: empty — starship handles the prompt
ZSH_THEME=""

plugins=(
  git       # git aliases and completions
  dotenv    # auto-load .env files in project directories
  macos     # macOS-specific aliases
)

source "$ZSH/oh-my-zsh.sh"

# ============================================================================
# HOMEBREW PATHS
# ============================================================================
if [[ $(uname -m) == "arm64" ]]; then
  BREW_PREFIX="/opt/homebrew"
else
  BREW_PREFIX="/usr/local"
fi

# PostgreSQL client
export PATH="$BREW_PREFIX/opt/libpq/bin:$PATH"
export PKG_CONFIG_PATH="$BREW_PREFIX/opt/libpq/lib/pkgconfig"

# OpenSSL
export PATH="$BREW_PREFIX/opt/openssl@3/bin:$PATH"
export LDFLAGS="-L$BREW_PREFIX/opt/openssl@3/lib"
export CPPFLAGS="-I$BREW_PREFIX/opt/openssl@3/include"

# Ruby (Homebrew-managed)
export PATH="$BREW_PREFIX/opt/ruby/bin:$PATH"

# ============================================================================
# PYTHON (pyenv) — lazy init
# ============================================================================
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv 1>/dev/null 2>&1; then
  # Clear stale lock if present
  [[ -f "$PYENV_ROOT/shims/.pyenv-shim" ]] && rm -f "$PYENV_ROOT/shims/.pyenv-shim"
  eval "$(pyenv init --path)"
  eval "$(pyenv init - --no-rehash)"
fi

# ============================================================================
# NODE (nvm) — lazy load via brew
# ============================================================================
export NVM_DIR="$HOME/.nvm"
nvm() {
  unset -f nvm
  [ -s "/opt/homebrew/opt/nvm/nvm.sh" ] && \. "/opt/homebrew/opt/nvm/nvm.sh"
  nvm "$@"
}

# ============================================================================
# RUBY (chruby)
# ============================================================================
if [ -f "$BREW_PREFIX/opt/chruby/share/chruby/chruby.sh" ]; then
  source "$BREW_PREFIX/opt/chruby/share/chruby/chruby.sh"
fi

# ============================================================================
# JAVA (SDKMAN) — install: https://sdkman.io/
# ============================================================================
export SDKMAN_DIR="$HOME/.sdkman"
[[ -s "$HOME/.sdkman/bin/sdkman-init.sh" ]] && source "$HOME/.sdkman/bin/sdkman-init.sh"

# ============================================================================
# CUSTOM PATHS
# ============================================================================
export PATH="$HOME/.local/bin:$PATH"
export PATH="$PATH:/usr/local/mysql/bin"
export PATH="$PATH:$(go env GOPATH 2>/dev/null)/bin"

# ============================================================================
# ZSH CONFIGS — load all *.zsh files under ~/.config/zsh/ recursively
# ============================================================================
# Workbench aliases are symlinked to config.d/. Work-specific configs go in
# any subdirectory (e.g. work/) and are picked up automatically.
for conf in "$HOME"/.config/zsh/**/*.zsh(.N); do
  source "${conf}"
done
unset conf

# ============================================================================
# SECRETS — local overrides, never committed
# ============================================================================
[ -f ~/.env.local ] && source ~/.env.local

# ============================================================================
# HISTORY
# ============================================================================
HISTTIMEFORMAT="%F %T "
HISTSIZE=10000
SAVEHIST=10000

# ============================================================================
# PROMPT (starship) — install: brew install starship
# ============================================================================
if command -v starship 1>/dev/null 2>&1; then
  eval "$(starship init zsh)"
fi

# ============================================================================
# KIRO CLI — post block (no-op if not installed)
# ============================================================================
[[ -f "${HOME}/Library/Application Support/kiro-cli/shell/zshrc.post.zsh" ]] && \
  builtin source "${HOME}/Library/Application Support/kiro-cli/shell/zshrc.post.zsh"
