# GPG — terminal and pinentry configuration
#
# Sets GPG_TTY so gpg can prompt for passphrases in the current terminal.
# Auto-configures pinentry-mac in gpg-agent.conf if not already set.
# No-op if gnupg is not installed.
#
# duplicate-check: GPG_TTY

command -v gpg >/dev/null 2>&1 || return 0

export GPG_TTY=$(tty)

# Auto-configure pinentry-mac if installed but not yet in gpg-agent.conf
if command -v pinentry-mac >/dev/null 2>&1; then
  local conf="$HOME/.gnupg/gpg-agent.conf"
  if [[ ! -f "$conf" ]] || ! grep -q "pinentry-program" "$conf"; then
    mkdir -p "$HOME/.gnupg"
    echo "pinentry-program $(command -v pinentry-mac)" >> "$conf"
    gpgconf --kill gpg-agent 2>/dev/null
  fi
fi
