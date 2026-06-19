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

  # Extract root package version: <details><summary>X.Y.Z</summary> (no component prefix)
  root_version=$(grep -oE '<details><summary>[0-9]+\.[0-9]+\.[0-9]+</summary>' "$BODY_FILE" \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)

  if [[ -n "$root_version" ]]; then
    tag="v${root_version}"
    if gh release view "$tag" --repo "$REPO" >/dev/null 2>&1; then
      echo "Release $tag already exists, skipping"
    else
      # Extract changelog between the <details> tags
      awk -v ver="$root_version" '
        $0 ~ "<details><summary>" ver "</summary>" { found=1; next }
        found && /<\/details>/ { exit }
        found { print }
      ' "$BODY_FILE" > "$NOTES_FILE"

      echo "Creating release $tag"
      if gh release create "$tag" \
          --repo "$REPO" \
          --target "$merge_sha" \
          --title "v${root_version}" \
          --notes-file "$NOTES_FILE"; then
        backfilled=$((backfilled + 1))
      else
        echo "::error::Failed to create release $tag"
        pr_ok=false
      fi
    fi
  fi

  # Extract claude-review version: <details><summary>claude-review: X.Y.Z</summary>
  cr_version=$(grep -oE '<details><summary>claude-review: [0-9]+\.[0-9]+\.[0-9]+</summary>' "$BODY_FILE" \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)

  if [[ -n "$cr_version" ]]; then
    tag="claude-review-v${cr_version}"
    if gh release view "$tag" --repo "$REPO" >/dev/null 2>&1; then
      echo "Release $tag already exists, skipping"
    else
      awk -v ver="claude-review: $cr_version" '
        $0 ~ "<details><summary>" ver "</summary>" { found=1; next }
        found && /<\/details>/ { exit }
        found { print }
      ' "$BODY_FILE" > "$NOTES_FILE"

      echo "Creating release $tag"
      if gh release create "$tag" \
          --repo "$REPO" \
          --target "$merge_sha" \
          --title "claude-review: v${cr_version}" \
          --notes-file "$NOTES_FILE"; then
        backfilled=$((backfilled + 1))
      else
        echo "::error::Failed to create release $tag"
        pr_ok=false
      fi
    fi
  fi

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
