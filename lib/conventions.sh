#!/usr/bin/env bash
# Git convention constants — single source of truth for commit and PR formatting.
#
# Sourced by lib/ai/core.sh (for AI automation tasks) and directly by scripts
# that only need convention constants (git/bin/generate-changelog, git/bin/generate-git-rules).
#
# To add a commit type, append it to COMMIT_TYPES — no other changes needed.

# shellcheck disable=SC2034  # All constants are used by sourcing scripts

# Maximum length of the commit header (type + optional scope + colon + space + subject).
# Enforced in both the AI prompt and the fallback validator.
COMMIT_HEADER_MAX_LEN=72

# Maximum length of each line in the commit body.
# Referenced in the AI prompt only — not machine-validated locally.
COMMIT_BODY_MAX_LEN=100

# Space-separated list of allowed commit types.
# Used to build the AI prompt rules and the fallback format validator.
COMMIT_TYPES="feat fix perf deps revert docs style refactor test build ci chore"
