# Shared helper for validate-migrations test files.
# Loaded in setup() so functions are available in tests.

# Helper: create a migration file in a component's migrations/ dir
_make_migration() {
  local component="$1" filename="$2" func_name="${3:-}"
  local dir="$FAKE_WORKBENCH/$component/migrations"
  mkdir -p "$dir"

  if [[ -z "$func_name" ]]; then
    # Derive function name from filename: 20260417-slug.sh -> migration_20260417_slug
    func_name="migration_${filename%.sh}"
    func_name="${func_name//-/_}"
  fi

  cat > "$dir/$filename" <<EOF
#!/usr/bin/env bash
${func_name}() {
  echo "migrating"
}
EOF
}

# Helper: run validate-migrations with WORKBENCH_DIR overridden
_run_validate() {
  WORKBENCH_DIR="$FAKE_WORKBENCH" NO_COLOR=1 run "$VALIDATE_MIGRATIONS" "$@"
}
