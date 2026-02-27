# macOS System Utilities

# ============================================================================
# System Information
# ============================================================================

alias path='echo -e ${PATH//:/\\n}'
alias ip='dig +short myip.opendns.com @resolver1.opendns.com'
alias list-ports='sudo lsof -PiTCP -sTCP:LISTEN'

# ============================================================================
# File Management
# ============================================================================

alias chdirs='find . -type d -exec chmod 755 {} \;'
alias chfiles='find . -type f -exec chmod 644 {} \;'
alias rm-build='find . -name "build" -type d -exec rm -rf {} \;'

# Make directory and cd into it
mkd() {
  mkdir -p "$@" && cd "$_" || return
}

# Extract most known archives with one command
extract() {
  if [ -f "$1" ]; then
    case "$1" in
      *.tar.bz2) tar xjf "$1" ;;
      *.tar.gz) tar xzf "$1" ;;
      *.bz2) bunzip2 "$1" ;;
      *.rar) unrar e "$1" ;;
      *.gz) gunzip "$1" ;;
      *.tar) tar xf "$1" ;;
      *.tbz2) tar xjf "$1" ;;
      *.tgz) tar xzf "$1" ;;
      *.zip) unzip "$1" ;;
      *.Z) uncompress "$1" ;;
      *.7z) 7z x "$1" ;;
      *) echo "'$1' cannot be extracted via extract()" ;;
    esac
  else
    echo "'$1' is not a valid file"
    return 1
  fi
}

# Determine size of a file or total size of a directory
fs() {
  if du -b /dev/null > /dev/null 2>&1; then
    local arg=-sbh
  else
    local arg=-sh
  fi
  if [[ -n "$@" ]]; then
    du $arg -- "$@"
  else
    du $arg .[^.]* * 2>/dev/null
  fi
}

# ============================================================================
# Finder
# ============================================================================

alias show-files='defaults write com.apple.finder AppleShowAllFiles -bool true && killall Finder'
alias hide-files='defaults write com.apple.finder AppleShowAllFiles -bool false && killall Finder'
alias show-desktop='defaults write com.apple.finder CreateDesktop -bool true && killall Finder'
alias hide-desktop='defaults write com.apple.finder CreateDesktop -bool false && killall Finder'

# ============================================================================
# Network & DNS
# ============================================================================

alias flush-dns='sudo killall -HUP mDNSResponder'

# ============================================================================
# Security
# ============================================================================

alias enable-gate='sudo spctl --master-enable'
alias disable-gate='sudo spctl --master-disable'
alias afk='/System/Library/CoreServices/Menu\ Extras/User.menu/Contents/Resources/CGSession -suspend'

# ============================================================================
# Chrome
# ============================================================================

alias kill-chrome="ps ux | grep '[C]hrome Helper --type=renderer' | grep -v extension-process | tr -s ' ' | cut -d ' ' -f2 | xargs kill"

# ============================================================================
# Homebrew
# ============================================================================

alias update='brew cleanup; brew upgrade; brew update; npm update -g; npm install npm@latest -g'

# ============================================================================
# Shell Configuration
# ============================================================================

alias reload='exec ${SHELL} -l'
alias copy-ssh='pbcopy < $HOME/.ssh/id-rsa.pub'

# Quick edit config files
alias edit-hosts='subl /etc/hosts'
alias edit-zshrc='subl ~/.zshrc'
alias edit-aws='subl ~/.config/zsh/config.d/aws.zsh'

# Alias discovery — 'aliases' is a script in ~/.local/bin; no alias needed
alias help-aliases='aliases'

# ============================================================================
# System Utilities
# ============================================================================

# Kill process running on specified port
port-kill() {
  if [ -z "$1" ]; then
    echo "Usage: port-kill <port-number>"
    return 1
  fi
  lsof -ti:"$1" | xargs kill -9
}
