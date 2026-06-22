#!/usr/bin/env bash
set -euo pipefail

# Update surviving release PRs after a release merges.
#
# With separate-pull-requests, each package gets its own release PR but
# they share .release-please-manifest.json. When one merges, the other
# conflicts on the manifest. This script:
#   1. Tries the GitHub API update-branch endpoint (fast path).
#   2. On merge conflict (HTTP 422), falls back to a local git merge
#      that auto-resolves the manifest by taking the highest semver
#      for each key.
#
# Runs in CI after release-please when a release was created.

readonly REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"
readonly MANIFEST=".github/.release-please-manifest.json"

git_configured=false

configure_git() {
  if [[ "$git_configured" == true ]]; then
    return
  fi
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git_configured=true
}

# Try the GitHub API fast path. Returns 0 on success, 1 on merge conflict.
try_api_update() {
  local pr="$1" sha="$2"
  local http_status

  http_status=$(gh api "repos/${REPO}/pulls/${pr}/update-branch" \
    -X PUT -f expected_head_sha="$sha" \
    --silent 2>&1) && {
    echo "  ✓ PR #${pr} updated via API"
    return 0
  }

  if echo "$http_status" | grep -q "merge conflict"; then
    return 1
  fi

  echo "::warning::Unexpected error updating PR #${pr}: ${http_status}"
  return 1
}

# Resolve the manifest by taking the highest version for each key.
resolve_manifest() {
  jq -n \
    --argjson ours "$(git show :2:"$MANIFEST")" \
    --argjson theirs "$(git show :3:"$MANIFEST")" \
    '[$ours, $theirs] | map(to_entries) | add | group_by(.key) |
     map(max_by(.value | split(".") | map(tonumber))) | from_entries' \
    > "$MANIFEST"
  git add "$MANIFEST"
}

# Fall back to a local merge with manifest auto-resolution.
try_local_merge() {
  local pr="$1" branch="$2"

  configure_git

  echo "  Attempting local merge for PR #${pr} (${branch})..."

  git checkout -B "$branch" "origin/${branch}"

  if git merge origin/main --no-edit 2>/dev/null; then
    echo "  ✓ PR #${pr} merged cleanly (no conflict after all)"
    git push origin "$branch"
    git checkout -
    return 0
  fi

  local conflicts
  conflicts=$(git diff --name-only --diff-filter=U)

  if [[ "$conflicts" != "$MANIFEST" ]]; then
    echo "::error::PR #${pr} has conflicts beyond the manifest: ${conflicts}"
    git merge --abort
    git checkout -
    return 1
  fi

  echo "  Resolving manifest conflict..."
  resolve_manifest

  git commit --no-edit
  git push origin "$branch"
  echo "  ✓ PR #${pr} updated via local merge with manifest resolution"

  git checkout -
  return 0
}

pending=$(gh pr list --repo "$REPO" --label "autorelease: pending" --state open \
  --json number,headRefOid,headRefName \
  --jq '.[] | [.number, .headRefOid, .headRefName] | @tsv')

if [[ -z "$pending" ]]; then
  echo "No pending release PRs to update"
  exit 0
fi

errors=0

while IFS=$'\t' read -r pr sha branch; do
  echo "::group::PR #${pr} (${branch})"
  echo "Updating PR #${pr} branch with latest main..."

  if try_api_update "$pr" "$sha"; then
    echo "::endgroup::"
    continue
  fi

  echo "  API update failed (merge conflict), falling back to local merge..."

  if ! try_local_merge "$pr" "$branch"; then
    errors=$((errors + 1))
  fi

  echo "::endgroup::"
done <<< "$pending"

if [[ "$errors" -gt 0 ]]; then
  echo "::error::Failed to update ${errors} release PR(s)"
  exit 1
fi
