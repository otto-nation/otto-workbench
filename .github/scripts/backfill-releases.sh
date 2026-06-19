#!/usr/bin/env bash
set -euo pipefail

# Backfill stale release PRs that release-please failed to tag.
#
# Finds merged PRs with "autorelease: pending" label, creates missing
# GitHub Releases from the PR body changelog, and relabels them as
# "autorelease: tagged" to unblock future release-please runs.
#
# Runs in CI after the release-please action when no releases were created.

readonly REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"

cleanup() {
  rm -f "$BODY_FILE" "$NOTES_FILE"
}

BODY_FILE=$(mktemp)
NOTES_FILE=$(mktemp)
trap cleanup EXIT

# Create a release if the tag doesn't already exist.
# Extracts changelog from $BODY_FILE using the summary_key.
# Returns 0 on success or if already exists, 1 on failure.
create_release() {
  local tag="$1" title="$2" summary_key="$3" merge_sha="$4"

  if gh release view "$tag" --repo "$REPO" >/dev/null 2>&1; then
    echo "Release $tag already exists, skipping"
    return 0
  fi

  awk -v ver="$summary_key" '
    $0 ~ "<details><summary>" ver "</summary>" { found=1; next }
    found && /<\/details>/ { exit }
    found { print }
  ' "$BODY_FILE" > "$NOTES_FILE"

  echo "Creating release $tag"
  gh release create "$tag" \
    --repo "$REPO" \
    --target "$merge_sha" \
    --title "$title" \
    --notes-file "$NOTES_FILE"
}

# Try to backfill a single release. Sets pr_ok=false on failure.
# Args: version tag title summary_key merge_sha
try_backfill() {
  local version="$1" tag="$2" title="$3" summary_key="$4" merge_sha="$5"
  [[ -n "$version" ]] || return 0

  if create_release "$tag" "$title" "$summary_key" "$merge_sha"; then
    backfilled=$((backfilled + 1))
    return 0
  fi
  echo "::error::Failed to create release $tag"
  pr_ok=false
}

pending_prs=$(gh pr list \
  --repo "$REPO" \
  --state merged \
  --label "autorelease: pending" \
  --json number,mergeCommit,body \
  --jq 'sort_by(.number)')

count=$(echo "$pending_prs" | jq 'length')
if [[ "$count" -eq 0 ]]; then
  echo "No stale release PRs to backfill"
  exit 0
fi

echo "Found $count stale release PR(s) to backfill"

backfilled=0
errors=0

while IFS= read -r pr; do
  pr_num=$(echo "$pr" | jq -r '.number')
  merge_sha=$(echo "$pr" | jq -r '.mergeCommit.oid')

  echo "::group::PR #$pr_num (commit $merge_sha)"
  echo "$pr" | jq -r '.body' > "$BODY_FILE"

  pr_ok=true

  root_version=$(grep -oE '<details><summary>[0-9]+\.[0-9]+\.[0-9]+</summary>' "$BODY_FILE" \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
  try_backfill "$root_version" "v${root_version}" "v${root_version}" "$root_version" "$merge_sha"

  cr_version=$(grep -oE '<details><summary>claude-review: [0-9]+\.[0-9]+\.[0-9]+</summary>' "$BODY_FILE" \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
  try_backfill "$cr_version" "claude-review-v${cr_version}" "claude-review: v${cr_version}" "claude-review: $cr_version" "$merge_sha"

  if [[ "$pr_ok" == true ]]; then
    gh pr edit "$pr_num" --repo "$REPO" \
      --remove-label "autorelease: pending" \
      --add-label "autorelease: tagged"
    echo "Relabeled PR #$pr_num: autorelease: pending → autorelease: tagged"
  else
    echo "::warning::Skipping relabel for PR #$pr_num due to errors"
    errors=$((errors + 1))
  fi

  echo "::endgroup::"
done < <(echo "$pending_prs" | jq -c '.[]')

echo "Backfilled $backfilled release(s), $errors error(s)"
