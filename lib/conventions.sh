#!/usr/bin/env bash
# Git convention constants — single source of truth for commit and PR formatting.
#
# Constants: `COMMIT_TYPES`, `COMMIT_HEADER_MAX_LEN`, `COMMIT_BODY_MAX_LEN`,
# `BREAKING_CHANGE_FOOTER`, `BREAKING_CHANGE_FOOTER_ALT`, `NOT_BREAKING_FOOTER`,
# `BREAKING_FOOTER_RE`. To add a commit type, append it to
# `COMMIT_TYPES` — no other change is needed.
#
# has_breaking_footer is called only by bin/local/check-surface-compat.
# git/bin/local/generate-git-rules and git/bin/generate-changelog source the
# constants and render them. ai/lib/core/conventions.py parses COMMIT_TYPES=
# as text; it does not source this file.
#
# No current caller needs POSIX — every sourcer is bash. The file stays POSIX
# (no `[[`, no `<<<`, no pattern-replacement expansion) so the bats cases that
# source it under sh keep holding that property.

# shellcheck disable=SC2034  # All constants are used by sourcing scripts

# Maximum length of the commit header (type + optional scope + colon + space + subject).
# Rendered into git.generated.md by git/bin/local/generate-git-rules; checked by
# ai/lib/core/conventions.py.
COMMIT_HEADER_MAX_LEN=72

# Maximum length of each line in the commit body.
# Rendered into git.generated.md by git/bin/local/generate-git-rules; not
# machine-validated locally.
COMMIT_BODY_MAX_LEN=100

# Space-separated list of allowed commit types.
# Rendered into git.generated.md by git/bin/local/generate-git-rules; read by
# git/bin/generate-changelog and ai/lib/core/conventions.py.
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
# bashism, and this file stays POSIX.
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
