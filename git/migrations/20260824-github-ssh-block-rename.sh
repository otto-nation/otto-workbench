#!/usr/bin/env bash
# Migration: drop the legacy github-ssh-443 block from ~/.ssh/config.
#
# step_github_ssh now owns a single block, marked `github-ssh`, that carries the
# SSH keepalive on every machine and the port 443 routing only when
# github.ssh_over_443 asks for it. A machine that opted into 443 under the old
# markers still has that block, and ssh keeps the first value it reads for each
# keyword: left in place it pins github.com to port 443 for good, outliving the
# config key being turned off. The step rewrites the current block on the same
# sync — migrations run first — so removing the old one is all that is needed
# here.
#
# A machine that never opted in has no such block and answers MIGRATION_NOOP.

migration_20260824_github_ssh_block_rename() {
  local begin="# >>> otto-workbench: github-ssh-443 >>>"
  local end="# <<< otto-workbench: github-ssh-443 <<<"

  [[ -f "$SSH_CONFIG_FILE" ]] || return "$MIGRATION_NOOP"
  grep -qF "$begin" "$SSH_CONFIG_FILE" || return "$MIGRATION_NOOP"

  # A block a hand edit left open has no end marker to stop at, and stripping
  # from the begin marker to end-of-file would take the rest of the user's
  # config with it. Report it and leave the file alone, which is what the step
  # does with the same damage.
  if ! grep -qF "$end" "$SSH_CONFIG_FILE"; then
    warn "$SSH_CONFIG_FILE has the github-ssh-443 begin marker but no end marker — leaving the file untouched"
    return "$MIGRATION_NOOP"
  fi

  # The scratch file is colocated with the destination so the mv is a rename
  # within one filesystem, and it carries mode 600 before the move so the config
  # is never briefly readable by anyone else.
  local tmp
  tmp="$(mktemp "$SSH_CONFIG_FILE.XXXXXX")"
  awk -v begin="$begin" -v end="$end" '
    $0 == begin { dropping = 1 }
    dropping != 1 { print }
    $0 == end { dropping = 0 }
  ' "$SSH_CONFIG_FILE" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$SSH_CONFIG_FILE"

  success "Removed the superseded github-ssh-443 block from $SSH_CONFIG_FILE"
}
