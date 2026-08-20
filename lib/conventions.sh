#!/usr/bin/env bash
# Git convention constants — single source of truth for commit and PR formatting.
#
# Sourced by lib/ai/core.sh (for AI automation tasks) and directly by scripts
# that only need convention constants (git/bin/generate-changelog, git/bin/generate-git-rules).
#
# To add a commit type, append it to COMMIT_TYPES — no other changes needed.
#
# The footer helper at the bottom is here for the same reason the tokens are:
# "this message declares a breaking change" is asked by the pre-push gate
# (bin/local/check-surface-compat) and by the local commit validator, and the
# two must not drift apart. The file is sourced by /bin/sh on the go-task path,
# so everything below stays POSIX — no [[, no <<<, no pattern-replacement
# expansion.

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

# Footer token that marks a breaking change. Release-please reads it from the
# squashed commit body to cut a major.
#
# The subject-level `!` marker is deliberately not sufficient on its own: the
# repo squash-merges with squash_merge_commit_title=COMMIT_OR_PR_TITLE, so on a
# multi-commit PR the PR title replaces the subject and the marker is lost.
# Commit bodies are always concatenated into the squashed message and survive.
BREAKING_CHANGE_FOOTER="BREAKING CHANGE"

# The hyphenated synonym, derived from the one constant rather than declared a
# second time — a rename of BREAKING_CHANGE_FOOTER carries both forms with it.
# Conventional Commits v1.0.0 lists it as a synonym and release-please honours
# it, so every reader below accepts either spelling.
#
# tr rather than "${BREAKING_CHANGE_FOOTER/ /-}": pattern replacement is a
# bashism, and this file is sourced by dash on the go-task path.
BREAKING_CHANGE_FOOTER_ALT=$(printf '%s' "$BREAKING_CHANGE_FOOTER" | tr ' ' '-')

# Footer recording a public-surface removal that is deliberately not breaking.
# Format: Not-Breaking: <surface entry> — <reason>
# One footer per removed entry. Read by bin/local/check-surface-compat.
NOT_BREAKING_FOOTER="Not-Breaking"

# ERE matching a footer line that declares a breaking change, either spelling.
# The reason is not optional: ": .+" is what separates a declaration from a
# bare token, because the reason landing in git history is the whole point.
BREAKING_FOOTER_RE="^(${BREAKING_CHANGE_FOOTER}|${BREAKING_CHANGE_FOOTER_ALT}): .+"

# has_breaking_footer MSG — true when MSG declares a breaking change in its body.
#
# The subject-level `!` marker is deliberately not consulted, here or anywhere
# else: see BREAKING_CHANGE_FOOTER for why a squash loses it. A caller that
# wants to know whether a `!` header is backed by a footer asks this about the
# whole message, not about the header.
has_breaking_footer() {
  printf '%s\n' "$1" | grep -qE "$BREAKING_FOOTER_RE"
}
