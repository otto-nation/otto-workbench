#!/usr/bin/env bats
# site/app/tokens.css calls itself "the single owner of these values in this repo"
# and carries the contrast contract for each one. A hex literal anywhere else is
# a second owner: it sits outside that contract, and a palette change never
# reaches it. This is the mechanical form of that claim.

setup() {
  load 'test_helper'
  common_setup
  TOKENS="site/app/tokens.css"
}

teardown() {
  common_teardown
}

@test "tokens.css is the only site source with hex color literals" {
  local offenders
  offenders="$(grep -rnE '#[0-9a-fA-F]{3,8}\b' \
    "$REPO_ROOT/site/app" "$REPO_ROOT/site/components" "$REPO_ROOT/site/lib" "$REPO_ROOT/site/mdx" \
    --include='*.tsx' --include='*.ts' --include='*.css' --include='*.mjs' \
    | grep -vF "$TOKENS:" || true)"
  [ -z "$offenders" ] || {
    echo "hex literals outside $TOKENS — reference an --ow-* token instead:"
    printf '%s\n' "$offenders" | sed 's/^/  /'
    return 1
  }
}

@test "every --ow-* token a component references is declared in tokens.css" {
  local undeclared=()
  local token
  while read -r token; do
    grep -qF -- "  $token:" "$REPO_ROOT/$TOKENS" || undeclared+=("$token")
  done < <(grep -rhoE --include='*.tsx' --include='*.ts' -e '--ow-[a-z-]+' \
    "$REPO_ROOT/site/app" "$REPO_ROOT/site/components" | sort -u)
  [ "${#undeclared[@]}" -eq 0 ] || {
    echo "referenced but not declared in $TOKENS: ${undeclared[*]}"
    return 1
  }
}
