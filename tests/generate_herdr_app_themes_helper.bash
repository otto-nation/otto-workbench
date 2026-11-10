# Shared fixtures for the generate-herdr-app-themes suites.
# Loaded in setup() after test_helper; each suite sets GEN, PAL and OUT before calling these.
# Each suite names the generator in its own select-on line: bin/local/select-tests maps a suite
# to what it covers by the path literals in the .bats file, not in its helpers.

_palette() {
  cat > "$PAL/test.toml" <<EOF
[palette]
ink = "#111111"
paper = "#eeeeee"

[themes.sample]
background = "$1"
ansi = ["ink", "paper"]

[themes.sample.colors]
accent = "ink"
EOF
}

_tokens() {
  cat > "$BATS_TEST_TMPDIR/tokens.json" <<'EOF'
{
  "schemaVersion": 1,
  "colors": {
    "light": {
      "canvas": "#f7f2e9",
      "only-root": "#abcdef"
    },
    "dark": {
      "canvas": "#0f0e13",
      "only-root": "#abcdef"
    }
  },
  "typography": {
    "meta": { "fontSize": "11px", "fontWeight": 400, "lineHeight": "16px" },
    "section": { "fontSize": "11px", "fontWeight": 600, "lineHeight": "16px" },
    "label": { "fontSize": "12px", "fontWeight": 500, "lineHeight": "16px" },
    "body": { "fontSize": "13px", "fontWeight": 400, "lineHeight": "18px" },
    "title": { "fontSize": "14px", "fontWeight": 600, "lineHeight": "20px" },
    "code": { "fontSize": "12px", "fontWeight": 400, "lineHeight": "16px" }
  },
  "lengths": {
    "control-height": "21px",
    "control-height-dense": "22px",
    "row-height": "33px",
    "row-height-dense": "34px",
    "control-padding": "15px",
    "card-padding": "16px",
    "column-min": "270px"
  },
  "radii": { "sm": "4px", "md": "6px", "lg": "8px" },
  "spacing": { "1": "4px", "2": "8px", "3": "12px", "4": "16px", "5": "20px", "6": "24px" },
  "panes": { "compact": "150px", "board": "550px", "wide": "1050px" }
}
EOF
}

_brand_palette() {
  cat > "$PAL/test.toml" <<EOF
[palette]
canvas = "brand:ow-canvas"
only = "brand:${1:-ow-only-root}"

[schemes]
sample-light = "light"
sample-dark = "${2:-dark}"

[themes.sample-light]
background = "canvas"

[themes.sample-dark]
background = "canvas"
accent = "only"
EOF
}

_brand_site() {
  local site="$BATS_TEST_TMPDIR/site" url="file://${BATS_TEST_TMPDIR}/brand.tgz"
  mkdir -p "$site"
  if [[ "${3:-}" == none ]]; then
    printf '%s\n' '{}' > "$site/package.json"
  else
    printf '%s\n' "{\"dependencies\": {\"@otto-nation/brand\": \"$url\"}}" > "$site/package.json"
  fi
  printf '%s\n' "{\"packages\": {\"node_modules/@otto-nation/brand\": {\"version\": \"1.2.0\", \"resolved\": \"${2:-$url}\", \"integrity\": \"sha512-fake\"}}}" > "$site/package-lock.json"
  [[ -z "${1:-}" ]] && return 0
  mkdir -p "$site/node_modules/@otto-nation/brand"
  printf '%s\n' "{\"version\": \"$1\"}" > "$site/node_modules/@otto-nation/brand/package.json"
  _tokens
  cp "$BATS_TEST_TMPDIR/tokens.json" "$site/node_modules/@otto-nation/brand/tokens.json"
}

_brand_tarball() {
  local root="$BATS_TEST_TMPDIR/pkg" tgz="$BATS_TEST_TMPDIR/brand.tgz" integrity
  mkdir -p "$root/package"
  _tokens
  cp "$BATS_TEST_TMPDIR/tokens.json" "$root/package/tokens.json"
  COPYFILE_DISABLE=1 tar -czf "$tgz" -C "$root" package
  integrity="$(python3 -c 'import base64, hashlib, pathlib, sys
print("sha512-" + base64.b64encode(hashlib.sha512(pathlib.Path(sys.argv[1]).read_bytes()).digest()).decode())' "$tgz")"
  mkdir -p "$BATS_TEST_TMPDIR/site"
  printf '%s\n' "{\"dependencies\": {\"@otto-nation/brand\": \"file://${tgz}\"}}" > "$BATS_TEST_TMPDIR/site/package.json"
  printf '%s\n' "{\"packages\": {\"node_modules/@otto-nation/brand\": {\"version\": \"1.2.0\", \"resolved\": \"file://${tgz}\", \"integrity\": \"${integrity}\"}}}" > "$BATS_TEST_TMPDIR/site/package-lock.json"
}

_terminal_dir() {
  local dir="$BATS_TEST_TMPDIR/terminal"
  mkdir -p "$dir"
  python3 - "$dir" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])

def theme(bg, fg):
    return {
        "background": bg,
        "foreground": fg,
        "selection": "#222222",
        "cursor": "#333333",
        "ansi": [f"#{i:02x}{i:02x}{i:02x}" for i in range(16)],
    }

(root / "otto-light.json").write_text(json.dumps(theme("#aaaaaa", "#111111"), indent=2) + "\n")
(root / "otto-dark.json").write_text(json.dumps(theme("#010101", "#fefefe"), indent=2) + "\n")
PY
}

_terminal_palette() {
  cat > "$PAL/test.toml" <<'EOF'
[palette]
canvas = "brand:ow-canvas"

[schemes]
sample-light = "light"
sample-dark = "dark"

[themes.sample-light]
terminal = "brand"

[themes.sample-light.colors]
accent = "canvas"

[themes.sample-dark]
terminal = "brand"

[themes.sample-dark.colors]
accent = "canvas"
EOF
}
